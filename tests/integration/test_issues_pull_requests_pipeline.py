"""Real PostgreSQL loads for mixed issue pages, PR hydration and atomic checkpoints."""

import hashlib
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from psycopg import sql

from ingestion.client import GitHubClient
from ingestion.db.migrate import migrate
from ingestion.extractors.commits import CommitExtractor
from ingestion.extractors.issues import IssueExtractor
from ingestion.extractors.pull_requests import PullRequestExtractor
from ingestion.loaders.commit_loader import CommitLoader
from ingestion.loaders.postgres_loader import ConcurrentRunError
from ingestion.loaders.raw_loader import RawLoader
from ingestion.loaders.updated_entity_loader import IssueLoader, PullRequestLoader
from ingestion.main import main
from ingestion.services.commit_service import CommitService
from ingestion.services.updated_entity_service import UpdatedEntityService

pytestmark = pytest.mark.integration
NAME = "octocat/Hello-World"
ENTITIES = ("issues", "pull_requests")


@pytest.fixture
def pipelines(
    writer,
    repository_payload,
    issue_payload,
    pull_request_payload,
    pull_request_issue_payload,
    commit_payload,
    tmp_path,
):
    closed = {
        **deepcopy(issue_payload),
        "id": 1003,
        "number": 3,
        "state": "closed",
        "closed_at": "2026-09-27T12:00:00Z",
    }
    closed_pr = {
        **deepcopy(pull_request_payload),
        "id": 9004,
        "number": 4,
        "state": "closed",
        "closed_at": "2026-09-27T12:00:00Z",
    }
    merged_pr = {
        **deepcopy(closed_pr),
        "id": 9005,
        "number": 5,
        "merged_at": "2026-09-27T12:00:00Z",
    }
    state = SimpleNamespace(
        repository=repository_payload,
        items=[
            issue_payload,
            pull_request_issue_payload,
            closed,
            {**deepcopy(pull_request_issue_payload), "id": 1004, "number": 4},
            {**deepcopy(pull_request_issue_payload), "id": 1005, "number": 5},
        ],
        details={2: pull_request_payload, 4: closed_pr, 5: merged_pr},
        now=datetime(2026, 9, 27, 12, 2, tzinfo=UTC),
        requests=[],
        fail_page=None,
        fail_detail=None,
        retry_status=None,
        waits=[],
    )

    def handler(request):
        state.requests.append(request)
        path = request.url.path
        if path == f"/repos/{state.repository['full_name']}":
            return httpx.Response(200, json=deepcopy(state.repository))
        if path.endswith("/issues"):
            assert request.url.params["state"] == "all"
            assert request.url.params["sort"] == "created"
            assert request.url.params["direction"] == "asc"
            if state.retry_status:
                status, state.retry_status = state.retry_status, None
                return httpx.Response(status, headers={"Retry-After": "1"})
            page = int(request.url.params.get("page", 1))
            per_page = int(request.url.params["per_page"])
            if page == state.fail_page:
                return httpx.Response(503)
            items = sorted(
                state.items, key=lambda item: (item["created_at"], item["number"])
            )
            since = request.url.params.get("since")
            if since:
                cutoff = datetime.fromisoformat(since)
                items = [
                    i for i in items if datetime.fromisoformat(i["updated_at"]) > cutoff
                ]
            headers = {}
            if page * per_page < len(items):
                target = request.url.copy_set_param("page", str(page + 1))
                headers["Link"] = f'<{target}>; rel="next"'
            return httpx.Response(
                200,
                json=deepcopy(items[(page - 1) * per_page : page * per_page]),
                headers=headers,
            )
        if "/pulls/" in path:
            number = int(path.rsplit("/", 1)[-1])
            if number == state.fail_detail:
                return httpx.Response(503)
            return httpx.Response(200, json=deepcopy(state.details[number]))
        if "/git/ref/heads/" in path:
            return httpx.Response(
                200,
                json={
                    "ref": "refs/heads/main",
                    "object": {"sha": commit_payload["sha"], "type": "commit"},
                },
            )
        if path.endswith("/commits"):
            return httpx.Response(200, json=[commit_payload])
        return httpx.Response(404)

    state.handler = handler
    with GitHubClient(
        transport=httpx.MockTransport(handler),
        max_retries=1,
        sleep=state.waits.append,
        clock=lambda: 0,
    ) as client:
        services = {
            entity: UpdatedEntityService(
                extractor(client, per_page=2),
                RawLoader(tmp_path),
                loader(writer),
                clock=lambda: state.now,
            )
            for entity, extractor, loader in [
                ("issues", IssueExtractor, IssueLoader),
                ("pull_requests", PullRequestExtractor, PullRequestLoader),
            ]
        }
        yield services, state


def rows(writer, entity):
    return writer.execute(
        "SELECT status, records_extracted, records_loaded FROM raw.pipeline_runs "
        "WHERE pipeline_name=%s ORDER BY started_at, finished_at",
        (f"{entity}_ingestion",),
    ).fetchall()


def count(writer, entity):
    return writer.execute(
        sql.SQL("SELECT count(*) FROM raw.{}").format(sql.Identifier(entity))
    ).fetchone()[0]


def checkpoint(writer, entity):
    row = writer.execute(
        "SELECT watermark FROM raw.entity_checkpoints WHERE entity=%s", (entity,)
    ).fetchone()
    return row[0] if row else None


def test_mixed_pages_load_only_issues_and_hydrate_true_pr_ids(
    pipelines, writer, tmp_path
):
    services, state = pipelines
    for service in services.values():
        assert service.run((NAME,)) == 0
    assert writer.execute(
        "SELECT id, number, state FROM raw.issues ORDER BY number"
    ).fetchall() == [(1001, 1, "open"), (1003, 3, "closed")]
    assert writer.execute(
        "SELECT id, number, state, merged_at IS NOT NULL "
        "FROM raw.pull_requests ORDER BY number"
    ).fetchall() == [
        (9002, 2, "open", False),
        (9004, 4, "closed", False),
        (9005, 5, "closed", True),
    ]
    assert (
        writer.execute(
            "SELECT count(*) FROM raw.issues i JOIN raw.pull_requests p "
            "USING (repository_id, number)"
        ).fetchone()[0]
        == 0
    )
    assert rows(writer, "issues") == [("SUCCESS", 2, 2)]
    assert rows(writer, "pull_requests") == [("SUCCESS", 3, 3)]
    snapshots = list(tmp_path.rglob("page-*.json"))
    assert len(snapshots) == 6
    assert any(
        "pull_request" in i for p in snapshots for i in json.loads(p.read_text())
    )
    detail_paths = list(tmp_path.rglob("pull-request-*.json"))
    assert {json.loads(p.read_text())["id"] for p in detail_paths} == {9002, 9004, 9005}
    assert all(json.loads(p.read_text())["future_field"]["keep"] for p in detail_paths)


@pytest.mark.parametrize("entity", ENTITIES)
def test_repeat_load_is_idempotent_and_keeps_provenance(pipelines, writer, entity):
    services, state = pipelines
    service = services[entity]
    assert service.run((NAME,)) == 0
    original = writer.execute(
        sql.SQL("SELECT id, raw_path FROM raw.{} ORDER BY id").format(
            sql.Identifier(entity)
        )
    ).fetchall()
    state.now += timedelta(minutes=1)
    assert service.run((NAME,)) == 0
    assert rows(writer, entity)[-1][2] == 0
    assert (
        writer.execute(
            sql.SQL("SELECT id, raw_path FROM raw.{} ORDER BY id").format(
                sql.Identifier(entity)
            )
        ).fetchall()
        == original
    )
    assert checkpoint(writer, entity) == state.now


@pytest.mark.parametrize("entity", ENTITIES)
def test_incremental_updates_and_ignores_old_unchanged_records(
    pipelines, writer, entity
):
    services, state = pipelines
    service = services[entity]
    assert service.run((NAME,)) == 0
    old_checkpoint = checkpoint(writer, entity)
    # Advance once with no changes; the next discovery uses that successful window.
    state.now += timedelta(minutes=10)
    assert service.run((NAME,)) == 0
    previous = checkpoint(writer, entity)
    number = 1 if entity == "issues" else 2
    source = next(item for item in state.items if item["number"] == number)
    source.update(
        updated_at=state.now.isoformat(),
        state="closed",
        closed_at=state.now.isoformat(),
    )
    if entity == "pull_requests":
        state.details[number].update(
            updated_at=state.now.isoformat(),
            state="closed",
            closed_at=state.now.isoformat(),
            merged_at=state.now.isoformat(),
            draft=False,
        )
    state.now += timedelta(minutes=1)
    state.requests.clear()
    assert service.run((NAME,)) == 0
    assert rows(writer, entity)[-1] == ("SUCCESS", 1, 1)
    request = next(r for r in state.requests if r.url.path.endswith("/issues"))
    assert datetime.fromisoformat(request.url.params["since"]) == previous - timedelta(
        minutes=5
    )
    assert checkpoint(writer, entity) > old_checkpoint
    value = writer.execute(
        sql.SQL("SELECT state FROM raw.{} WHERE number=%s").format(
            sql.Identifier(entity)
        ),
        (number,),
    ).fetchone()
    assert value == ("closed",)


@pytest.mark.parametrize("entity", ENTITIES)
def test_full_refresh_deduplicates_and_rejects_stale_updates(pipelines, writer, entity):
    services, state = pipelines
    service = services[entity]
    assert service.run((NAME,)) == 0
    state.items += deepcopy(state.items)
    assert service.run((NAME,), full_refresh=True) == 0
    assert rows(writer, entity)[-1][1:] == ((4, 0) if entity == "issues" else (6, 0))
    source = state.items[0] if entity == "issues" else state.details[2]
    source.update(title="stale title", updated_at="2026-09-10T00:00:00Z")
    assert service.run((NAME,), full_refresh=True) == 0
    assert (
        writer.execute(
            sql.SQL("SELECT count(*) FROM raw.{} WHERE title='stale title'").format(
                sql.Identifier(entity)
            )
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("entity", ENTITIES)
def test_empty_window_advances_only_its_entity_checkpoint(pipelines, writer, entity):
    services, state = pipelines
    state.items = []
    assert services[entity].run((NAME,)) == 0
    assert rows(writer, entity) == [("SUCCESS", 0, 0)]
    assert checkpoint(writer, entity) == state.now
    assert (
        checkpoint(writer, "issues" if entity == "pull_requests" else "pull_requests")
        is None
    )
    assert (
        writer.execute("SELECT count(*) FROM raw.ingestion_checkpoints").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("entity", ENTITIES)
@pytest.mark.parametrize("failure", ["page", "validation", "sql", "raw"])
def test_failure_rolls_back_data_and_checkpoint_then_retry_recovers(
    pipelines, writer, tmp_path, monkeypatch, entity, failure
):
    services, state = pipelines
    service = services[entity]
    assert service.run((NAME,)) == 0
    old_checkpoint = checkpoint(writer, entity)
    state.now += timedelta(minutes=1)
    selected = state.items[0] if entity == "issues" else state.details[2]
    original = deepcopy(selected)
    selected.update(title="new title", updated_at=state.now.isoformat())
    good = deepcopy(selected)
    save = service.raw.save_document
    if failure == "page":
        state.fail_page = 2
    elif failure == "validation":
        selected["state"] = "invalid"
    elif failure == "sql":
        selected["title"] = "SQL rejects \u0000"
    else:

        def fail_raw(*args):
            if args[3].startswith("page-"):
                raise OSError("disk full")
            return save(*args)

        monkeypatch.setattr(service.raw, "save_document", fail_raw)
    assert service.run((NAME,)) == 1
    assert checkpoint(writer, entity) == old_checkpoint
    assert rows(writer, entity)[-1][0] == "FAILED" and rows(writer, entity)[-1][2] == 0
    assert (
        writer.execute(
            sql.SQL("SELECT title FROM raw.{} WHERE id=%s").format(
                sql.Identifier(entity)
            ),
            (original["id"],),
        ).fetchone()[0]
        == original["title"]
    )
    assert list(tmp_path.rglob("page-*.json"))
    selected.clear()
    selected.update(good)
    state.fail_page = None
    monkeypatch.setattr(service.raw, "save_document", save)
    assert service.run((NAME,)) == 0
    assert checkpoint(writer, entity) == state.now
    assert rows(writer, entity)[-1][2] == 1


def test_pr_detail_failure_does_not_advance_either_checkpoint(pipelines, writer):
    services, state = pipelines
    state.fail_detail = 4
    assert services["pull_requests"].run((NAME,)) == 1
    assert count(writer, "pull_requests") == 0
    assert checkpoint(writer, "pull_requests") is None
    assert checkpoint(writer, "issues") is None
    state.fail_detail = None
    assert services["pull_requests"].run((NAME,)) == 0
    assert count(writer, "pull_requests") == 3


def test_wrong_pr_identity_fails_after_preserving_response(pipelines, writer, tmp_path):
    services, state = pipelines
    state.details[2]["number"] = 99
    assert services["pull_requests"].run((NAME,)) == 1
    path = next(tmp_path.rglob("pull-request-2.json"))
    assert json.loads(path.read_text())["number"] == 99
    assert checkpoint(writer, "pull_requests") is None
    assert count(writer, "pull_requests") == 0


@pytest.mark.parametrize("entity", ENTITIES)
def test_checkpoint_uses_discovery_start_not_future_source_time(
    pipelines, writer, entity
):
    services, state = pipelines
    for item in state.items:
        item["updated_at"] = "2099-01-01T00:00:00Z"
    for item in state.details.values():
        item["updated_at"] = "2099-01-01T00:00:00Z"
    assert services[entity].run((NAME,)) == 0
    assert checkpoint(writer, entity) == state.now
    # A clock correction or a forced full refresh cannot move the checkpoint back.
    state.now -= timedelta(hours=1)
    assert services[entity].run((NAME,), full_refresh=True) == 0
    assert checkpoint(writer, entity) == state.now + timedelta(hours=1)


@pytest.mark.parametrize("entity", ENTITIES)
def test_success_audit_failure_rolls_back_entity_and_checkpoint(
    pipelines, writer, monkeypatch, entity
):
    services, state = pipelines
    service = services[entity]

    def fail(*args):
        raise ValueError("audit unavailable")

    monkeypatch.setattr(service.postgres, "finish_run", fail)
    assert service.run((NAME,)) == 1
    assert count(writer, entity) == 0
    assert checkpoint(writer, entity) is None
    assert rows(writer, entity)[-1][0] == "FAILED"


@pytest.mark.parametrize("entity", ENTITIES)
def test_repository_number_unique_constraint(pipelines, writer, entity):
    services, state = pipelines
    assert services[entity].run((NAME,)) == 0
    with pytest.raises(psycopg.errors.UniqueViolation):
        # A different API ID cannot create a second conceptual item at this number.
        writer.execute(
            sql.SQL(
                "UPDATE raw.{} SET number=(SELECT min(number) FROM raw.{}) "
                "WHERE number=(SELECT max(number) FROM raw.{})"
            ).format(*[sql.Identifier(entity)] * 3)
        )


@pytest.mark.parametrize("status", [429, 503])
def test_discovery_reuses_rate_limit_and_retry(pipelines, writer, status):
    services, state = pipelines
    state.retry_status = status
    assert services["issues"].run((NAME,)) == 0
    assert state.waits and count(writer, "issues") == 2


def test_locks_are_separate_by_entity_and_conflict_with_same_entity(
    writer, database_settings
):
    with psycopg.connect(
        **database_settings.connection_kwargs(), autocommit=True
    ) as other:
        with IssueLoader(writer).lock(123):
            with PullRequestLoader(other).lock(123):
                pass
            with pytest.raises(ConcurrentRunError):
                with IssueLoader(other).lock(123):
                    pytest.fail("Acquired another session's issue lock")


@pytest.mark.parametrize("entity", ENTITIES)
def test_same_number_and_checkpoint_are_scoped_to_repository(pipelines, writer, entity):
    services, state = pipelines
    service = services[entity]
    assert service.run((NAME,)) == 0
    initial_count = count(writer, entity)
    state.repository.update(
        id=state.repository["id"] + 1, name="second", full_name="octocat/second"
    )
    for item in state.items:
        item["id"] += 10000
    for item in state.details.values():
        item["id"] += 10000
    assert service.run(("octocat/second",)) == 0
    assert count(writer, entity) == 2 * initial_count
    assert (
        writer.execute(
            "SELECT count(*) FROM raw.entity_checkpoints WHERE entity=%s", (entity,)
        ).fetchone()[0]
        == 2
    )


def test_reopened_issue_updates_state_and_comment_count(pipelines, writer):
    services, state = pipelines
    service = services["issues"]
    assert service.run((NAME,)) == 0
    item = next(item for item in state.items if item["number"] == 3)
    state.now += timedelta(minutes=1)
    item.update(
        state="open", closed_at=None, comments=42, updated_at=state.now.isoformat()
    )
    assert service.run((NAME,)) == 0
    assert writer.execute(
        "SELECT state, closed_at, comments_count FROM raw.issues WHERE number=3"
    ).fetchone() == ("open", None, 42)


@pytest.mark.parametrize("table", ["issues", "pull_requests", "entity_checkpoints"])
def test_new_tables_deny_delete_to_writer(writer, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer.execute(sql.SQL("DELETE FROM raw.{}").format(sql.Identifier(table)))


@pytest.mark.parametrize("command", ["issues", "pull-requests", "all"])
def test_cli_commands_and_all_preserve_existing_pipelines(
    pipelines, writer, database_settings, tmp_path, monkeypatch, capsys, command
):
    services, state = pipelines
    settings = database_settings
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "POSTGRES_HOST": settings.host,
        "POSTGRES_PORT": str(settings.port),
        "POSTGRES_DB": settings.database,
        "POSTGRES_USER": settings.admin_user,
        "GITLOG_DB_USER": settings.user,
        "GITLOG_DB_PASSWORD": settings.password.get_secret_value(),
        "GITLOG_REPOSITORIES": NAME,
    }.items():
        monkeypatch.setenv(key, value)
    logger = logging.getLogger("ingestion")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setattr(
        "ingestion.main.GitHubClient",
        lambda: GitHubClient(transport=httpx.MockTransport(state.handler)),
    )
    monkeypatch.setattr("sys.argv", ["gitlog", command, "--per-page", "2"])
    assert main() == 0
    assert main() == 0
    if command in ("issues", "all"):
        assert count(writer, "issues") == 2
    if command in ("pull-requests", "all"):
        assert count(writer, "pull_requests") == 3
    if command == "all":
        assert count(writer, "repositories") == 1
        assert count(writer, "commits") == 1
        assert (
            writer.execute("SELECT count(*) FROM raw.ingestion_checkpoints").fetchone()[
                0
            ]
            == 1
        )
        assert (
            writer.execute("SELECT count(*) FROM raw.entity_checkpoints").fetchone()[0]
            == 2
        )
        assert (
            writer.execute(
                "SELECT count(*) FROM raw.pipeline_runs WHERE status='SUCCESS'"
            ).fetchone()[0]
            == 8
        )
    output = capsys.readouterr().err
    assert settings.password.get_secret_value() not in output
    assert "Preserved raw" not in output
    events = [json.loads(line) for line in output.splitlines()]
    assert any(event.get("event") == "entity.completed" for event in events)


def test_migration_upgrade_preserves_existing_repository_and_commit_data(
    pipelines, admin, writer, database_settings, tmp_path
):
    services, state = pipelines
    # Set up a repository using the actual service, then emulate a Phase 3 schema.
    assert services["issues"].run((NAME,)) == 0
    assert (
        CommitService(
            CommitExtractor(services["issues"].extractor.client),
            RawLoader(tmp_path),
            CommitLoader(writer),
        ).run((NAME,))
        == 0
    )
    commits_before = writer.execute(
        "SELECT repository_id, sha FROM raw.commits"
    ).fetchall()
    checkpoint_before = writer.execute(
        "SELECT * FROM raw.ingestion_checkpoints"
    ).fetchall()
    repository_id = state.repository["id"]
    before = writer.execute("SELECT id, full_name FROM raw.repositories").fetchall()
    # Rebuild the actual Phase 3 schema in the runner's isolated database.
    # Snapshot source rows without Phase 5 audit columns, then upgrade forward.
    tables = ("pipeline_runs", "repositories", "commits", "ingestion_checkpoints")
    saved = {}
    for table in tables:
        cursor = admin.execute(
            sql.SQL("SELECT * FROM raw.{}").format(sql.Identifier(table))
        )
        columns = [column.name for column in cursor.description]
        keep = [
            i
            for i, name in enumerate(columns)
            if name not in ("duration_ms", "records_skipped")
        ]
        saved[table] = (
            [columns[i] for i in keep],
            [tuple(row[i] for i in keep) for row in cursor.fetchall()],
        )
    with admin.transaction():
        admin.execute("DROP SCHEMA raw CASCADE")
        admin.execute("DELETE FROM public.gitlog_schema_migrations")
        for migration in sorted(
            files("ingestion.db").joinpath("migrations").iterdir(),
            key=lambda item: item.name,
        ):
            if not migration.name.startswith(("001_", "002_")):
                continue
            content = migration.read_text(encoding="utf-8")
            admin.execute(content)
            admin.execute(
                "INSERT INTO public.gitlog_schema_migrations(version, checksum) "
                "VALUES (%s,%s)",
                (migration.name, hashlib.sha256(content.encode()).hexdigest()),
            )
        for table, (columns, data) in saved.items():
            for row in data:
                admin.execute(
                    sql.SQL("INSERT INTO raw.{} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns),
                    ),
                    row,
                )
        migrate(admin, database_settings)
    assert (
        writer.execute("SELECT id, full_name FROM raw.repositories").fetchall()
        == before
    )
    assert writer.execute(
        "SELECT id FROM raw.repositories WHERE id=%s", (repository_id,)
    ).fetchone()
    assert count(writer, "issues") == count(writer, "pull_requests") == 0
    assert (
        writer.execute("SELECT repository_id, sha FROM raw.commits").fetchall()
        == commits_before
    )
    assert (
        writer.execute("SELECT * FROM raw.ingestion_checkpoints").fetchall()
        == checkpoint_before
    )
