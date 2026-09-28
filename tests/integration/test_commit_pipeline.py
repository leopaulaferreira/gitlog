"""Real SQL transactions and checkpoints; deterministic multi-page GitHub responses."""

import json
import logging
from copy import deepcopy
from types import SimpleNamespace

import httpx
import psycopg
import pytest

from ingestion.client import GitHubClient
from ingestion.client.exceptions import GitHubNotFoundError
from ingestion.extractors.commits import CommitExtractor
from ingestion.loaders.commit_loader import CommitLoader, ConcurrentCommitRunError
from ingestion.loaders.raw_loader import RawLoader
from ingestion.main import main
from ingestion.services.commit_service import CommitService

pytestmark = pytest.mark.integration
NAME = "octocat/Hello-World"


@pytest.fixture
def pipeline(writer, repository_payload, commit_payload, tmp_path):
    state = SimpleNamespace(
        repository=repository_payload,
        history=[deepcopy(commit_payload)],
        delta=[],
        status="ahead",
        head=commit_payload["sha"],
        fail_page=None,
        total=None,
        empty=False,
        requests=[],
        retry_status=None,
        waits=[],
    )

    def handler(request):
        state.requests.append(request)
        path = request.url.path
        if path == f"/repos/{NAME}":
            return httpx.Response(200, json=state.repository)
        if "/git/ref/heads/" in path:
            if state.empty:
                return httpx.Response(409)
            return httpx.Response(
                200,
                json={
                    "ref": f"refs/heads/{state.repository['default_branch']}",
                    "object": {"sha": state.head, "type": "commit"},
                },
            )
        comparison = "/compare/" in path
        assert comparison or path.endswith("/commits")
        number = int(request.url.params.get("page", "1"))
        size = int(request.url.params["per_page"])
        if state.retry_status:
            status, state.retry_status = state.retry_status, None
            return httpx.Response(status, headers={"Retry-After": "1"})
        if state.fail_page == number:
            return httpx.Response(503)
        if comparison and state.status == "missing":
            return httpx.Response(404)
        if not comparison:
            assert request.url.params["sha"] == state.head
        items = state.delta if comparison else state.history
        page = deepcopy(items[(number - 1) * size : number * size])
        payload = (
            {
                "status": state.status,
                "total_commits": len(items) if state.total is None else state.total,
                "commits": page,
                "future_comparison_field": True,
            }
            if comparison
            else page
        )
        headers = {}
        if number * size < len(items):
            next_url = request.url.copy_set_param("page", str(number + 1))
            headers["Link"] = f'<{next_url}>; rel="next"'
        return httpx.Response(200, json=payload, headers=headers)

    with GitHubClient(
        transport=httpx.MockTransport(handler),
        max_retries=1,
        sleep=state.waits.append,
        clock=lambda: 0,
    ) as client:
        service = CommitService(
            CommitExtractor(client, per_page=1),
            RawLoader(tmp_path),
            CommitLoader(writer),
        )
        state.handler = handler
        yield service, state


def rows(writer):
    return writer.execute(
        "SELECT status, records_extracted, records_loaded "
        "FROM raw.pipeline_runs ORDER BY started_at"
    ).fetchall()


def checkpoint(writer):
    row = writer.execute("SELECT head_sha FROM raw.ingestion_checkpoints").fetchone()
    return row[0] if row else None


def count(writer):
    return writer.execute("SELECT count(*) FROM raw.commits").fetchone()[0]


def test_two_loads_preserve_pages_and_do_not_duplicate(pipeline, writer, tmp_path):
    service, state = pipeline
    state.history.append({**deepcopy(state.history[0]), "sha": "b" * 40})
    assert service.run((NAME,)) == 0
    assert count(writer) == 2
    assert checkpoint(writer) == state.head
    state.requests.clear()
    assert service.run((NAME,)) == 0
    assert count(writer) == 2
    assert rows(writer) == [("SUCCESS", 2, 2), ("SUCCESS", 0, 0)]
    assert all(
        "/compare/" not in r.url.path and not r.url.path.endswith("/commits")
        for r in state.requests
    )
    snapshots = list(tmp_path.rglob("page-*.json"))
    assert len(snapshots) == 2
    assert all(json.loads(p.read_text())[0]["future_field"] for p in snapshots)
    assert (
        writer.execute(
            "SELECT count(*) FROM (SELECT repository_id, sha FROM raw.commits "
            "GROUP BY repository_id, sha HAVING count(*) > 1) duplicates"
        ).fetchone()[0]
        == 0
    )


def test_incremental_load_includes_backdated_commits_from_merge(pipeline, writer):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.head = "c" * 40
    old = deepcopy(state.history[0])
    old["sha"] = "b" * 40
    old["commit"]["committer"]["date"] = "1999-01-01T00:00:00Z"
    new = {**deepcopy(old), "sha": state.head}
    state.delta = [old, new]
    assert service.run((NAME,)) == 0
    assert count(writer) == 3
    assert checkpoint(writer) == state.head
    assert rows(writer)[-1] == ("SUCCESS", 2, 2)
    comparisons = [r for r in state.requests if "/compare/" in r.url.path]
    assert len(comparisons) == 2
    assert all(f"{'a' * 40}...{'c' * 40}" in r.url.path for r in comparisons)


def test_full_refresh_deduplicates_pages_and_updates_changed_fields(pipeline, writer):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.history.append(deepcopy(state.history[0]))
    assert service.run((NAME,), full_refresh=True) == 0
    assert rows(writer)[-1] == ("SUCCESS", 2, 0)
    state.history[0]["author"] = {"login": "linked-later"}
    state.history[1] = deepcopy(state.history[0])
    assert service.run((NAME,), full_refresh=True) == 0
    assert count(writer) == 1
    assert rows(writer)[-1] == ("SUCCESS", 2, 1)
    assert (
        writer.execute("SELECT author_login FROM raw.commits").fetchone()[0]
        == "linked-later"
    )


def test_same_sha_can_belong_to_two_repositories(pipeline, writer):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.repository["id"] += 1
    assert service.run((NAME,)) == 0
    assert count(writer) == 2
    assert (
        writer.execute(
            "SELECT count(DISTINCT repository_id) FROM raw.commits"
        ).fetchone()[0]
        == 2
    )


@pytest.mark.parametrize("stage", ["page", "validation", "sql", "truncated"])
def test_failure_retains_checkpoint_and_raw_then_retry_recovers(
    pipeline, writer, tmp_path, stage
):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.head = "c" * 40
    state.delta = [
        {**deepcopy(state.history[0]), "sha": "b" * 40},
        {**deepcopy(state.history[0]), "sha": state.head},
    ]
    good = deepcopy(state.delta)
    if stage == "page":
        state.fail_page = 2
    elif stage == "validation":
        state.delta[1]["commit"]["committer"]["date"] = "not-a-date"
    elif stage == "sql":
        state.delta[1]["commit"]["message"] = "SQL cannot store \u0000"
    else:
        state.total = 3
    assert service.run((NAME,)) == 1
    assert checkpoint(writer) == "a" * 40
    assert count(writer) == 1
    assert rows(writer)[-1] == ("FAILED", 1 if stage == "page" else 2, 0)
    assert len(list(tmp_path.rglob("page-*.json"))) >= 2
    state.delta, state.fail_page, state.total = good, None, None
    assert service.run((NAME,)) == 0
    assert checkpoint(writer) == "c" * 40
    assert count(writer) == 3


def test_audit_failure_rolls_back_commits_and_checkpoint(pipeline, writer, monkeypatch):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.head = "b" * 40
    state.delta = [{**deepcopy(state.history[0]), "sha": state.head}]
    complete = service.postgres.complete

    # Invalidate the audit before loading so the final rowcount guard fails
    # after executing the commit UPSERT and checkpoint update.
    def already_finished(*args):
        service.postgres.fail_run(args[1], 0, None, "InjectedFailure")
        return complete(*args)

    monkeypatch.setattr(service.postgres, "complete", already_finished)
    with pytest.raises(ValueError, match="RUNNING"):
        service.run((NAME,))
    assert checkpoint(writer) == "a" * 40
    assert count(writer) == 1


@pytest.mark.parametrize("status", ["diverged", "behind", "missing"])
def test_rewritten_history_reconciles_without_removing_old_commits(
    pipeline, writer, status
):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.head = "b" * 40
    state.status = status
    state.history = [{**deepcopy(state.history[0]), "sha": state.head}]
    assert service.run((NAME,)) == 0
    assert checkpoint(writer) == state.head
    assert count(writer) == 2


def test_branch_change_forces_full_history(pipeline, writer):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.repository["default_branch"] = "release/v2"
    state.head = "b" * 40
    state.history.append({**deepcopy(state.history[0]), "sha": state.head})
    state.requests.clear()
    assert service.run((NAME,)) == 0
    assert count(writer) == 2
    assert not any("/compare/" in r.url.path for r in state.requests)
    assert (
        writer.execute("SELECT branch FROM raw.ingestion_checkpoints").fetchone()[0]
        == "release/v2"
    )


def test_empty_repository_is_success_without_checkpoint(pipeline, writer):
    service, state = pipeline
    state.empty = True
    assert service.run((NAME,)) == 0
    assert rows(writer) == [("SUCCESS", 0, 0)]
    assert checkpoint(writer) is None


@pytest.mark.parametrize("status", [429, 503])
def test_commit_pagination_reuses_rate_limit_and_retry(pipeline, writer, status):
    service, state = pipeline
    state.retry_status = status
    assert service.run((NAME,)) == 0
    assert count(writer) == 1
    assert state.waits and all(delay >= 1 for delay in state.waits)


def test_concurrent_repository_lock_is_rejected_and_released(writer, database_settings):
    loader = CommitLoader(writer)
    with psycopg.connect(
        **database_settings.connection_kwargs(), autocommit=True
    ) as other:
        contender = CommitLoader(other)
        with loader.lock(123):
            with pytest.raises(ConcurrentCommitRunError):
                with contender.lock(123):
                    pytest.fail("Concurrent access acquired the lock.")
        with contender.lock(123):
            assert contender.checkpoint(123, "main") is None


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM raw.commits",
        "TRUNCATE raw.commits",
        "DELETE FROM raw.ingestion_checkpoints",
        "ALTER TABLE raw.commits ADD test int",
    ],
)
def test_commit_writer_permissions_are_restricted(writer, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer.execute(statement)


def test_commits_cli_runs_twice(
    pipeline, writer, database_settings, tmp_path, monkeypatch, capsys
):
    service, state = pipeline
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
    # Fresh clients use the fixture's HTTP handler, preserving the CLI's real
    # configuration, database connection, extraction and loading paths.
    monkeypatch.setattr(
        "ingestion.main.GitHubClient",
        lambda: GitHubClient(transport=httpx.MockTransport(state.handler)),
    )
    monkeypatch.setattr("sys.argv", ["gitlog", "commits", "--per-page", "1"])
    assert main() == 0
    assert main() == 0
    assert count(writer) == 1
    assert rows(writer) == [("SUCCESS", 1, 1), ("SUCCESS", 0, 0)]
    output = capsys.readouterr().err
    assert settings.password.get_secret_value() not in output
    events = [json.loads(line) for line in output.splitlines()]
    completed = [e for e in events if e["event"] == "commits.completed"]
    assert completed[-1]["records_loaded"] == 0


def test_large_comparison_loads_more_than_250_commits(pipeline, writer):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.delta = [
        {**deepcopy(state.history[0]), "sha": f"{number:040x}"}
        for number in range(1, 252)
    ]
    state.head = state.delta[-1]["sha"]
    service.extractor.per_page = 100
    assert service.run((NAME,)) == 0
    assert count(writer) == 252
    assert rows(writer)[-1] == ("SUCCESS", 251, 251)
    assert len([r for r in state.requests if "/compare/" in r.url.path]) == 3


def test_raw_failure_prevents_commit_load_and_checkpoint(pipeline, writer, monkeypatch):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    state.head = "b" * 40
    state.delta = [{**deepcopy(state.history[0]), "sha": state.head}]
    save = service.raw.save_commit_document

    def fail_page(name, run_id, document, payload, instant):
        if document.startswith("page-"):
            raise OSError("disk full")
        return save(name, run_id, document, payload, instant)

    monkeypatch.setattr(service.raw, "save_commit_document", fail_page)
    assert service.run((NAME,)) == 1
    assert checkpoint(writer) == "a" * 40
    assert count(writer) == 1
    assert rows(writer)[-1] == ("FAILED", 1, 0)


def test_failed_repository_does_not_block_next_one(pipeline, writer, monkeypatch):
    service, state = pipeline
    extract = service.extractor.repository

    def repository(name):
        if name == "octocat/missing":
            raise GitHubNotFoundError(404)
        return extract(name)

    monkeypatch.setattr(service.extractor, "repository", repository)
    assert service.run(("octocat/missing", NAME)) == 1
    assert rows(writer) == [("FAILED", 0, 0), ("SUCCESS", 1, 1)]


def test_audit_start_failure_prevents_api_calls(pipeline, monkeypatch):
    service, state = pipeline

    def fail(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(service.postgres, "start_run", fail)
    with pytest.raises(OSError):
        service.run((NAME,))
    assert state.requests == []


def test_incomplete_history_without_pinned_head_does_not_advance(pipeline, writer):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    original = checkpoint(writer)
    state.head = "c" * 40
    state.delta = [{**deepcopy(state.history[0]), "sha": "b" * 40}]
    assert service.run((NAME,)) == 1
    assert checkpoint(writer) == original
    assert count(writer) == 1
    assert service.summaries[0]["error_type"] == "GitHubPaginationError"


@pytest.mark.parametrize("total", [True, -1, "2"])
def test_invalid_compare_total_preserves_checkpoint(pipeline, writer, total):
    service, state = pipeline
    assert service.run((NAME,)) == 0
    original = checkpoint(writer)
    state.head = "b" * 40
    state.delta = [{**deepcopy(state.history[0]), "sha": state.head}]
    state.total = total
    assert service.run((NAME,)) == 1
    assert checkpoint(writer) == original
    assert count(writer) == 1
