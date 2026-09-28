"""Failure isolation and observability against real, isolated PostgreSQL."""

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest

from ingestion.client import GitHubClient
from ingestion.extractors.commits import CommitExtractor
from ingestion.extractors.issues import IssueExtractor
from ingestion.extractors.pull_requests import PullRequestExtractor
from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.commit_loader import CommitLoader
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.loaders.raw_loader import RawLoader
from ingestion.loaders.updated_entity_loader import IssueLoader, PullRequestLoader
from ingestion.main import main
from ingestion.services.commit_service import CommitService
from ingestion.services.ingestion_service import IngestionService
from ingestion.services.updated_entity_service import UpdatedEntityService
from ingestion.status import print_status, snapshot

pytestmark = pytest.mark.integration
NAME = "octocat/Hello-World"


@pytest.fixture
def reliable_pipelines(
    writer,
    repository_payload,
    commit_payload,
    issue_payload,
    pull_request_payload,
    pull_request_issue_payload,
    tmp_path,
):
    state = SimpleNamespace(
        failures={},
        calls=[],
        waits=[],
        commits=[commit_payload],
        issues=[issue_payload, pull_request_issue_payload],
    )

    def handler(request):
        path = request.url.path
        state.calls.append(path)
        if path in state.failures:
            return httpx.Response(state.failures[path], headers={"Retry-After": "1"})
        if path == f"/repos/{NAME}":
            payload = repository_payload
        elif path.endswith("/issues"):
            payload = state.issues
        elif path.endswith("/pulls/2"):
            payload = pull_request_payload
        elif path.endswith("/git/ref/heads/main"):
            payload = {
                "ref": "refs/heads/main",
                "object": {"sha": commit_payload["sha"], "type": "commit"},
            }
        elif path.endswith("/commits"):
            payload = state.commits
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=deepcopy(payload))

    with GitHubClient(
        transport=httpx.MockTransport(handler),
        max_retries=1,
        sleep=state.waits.append,
        clock=lambda: 0,
    ) as client:
        raw = RawLoader(tmp_path)
        services = {
            "repositories": IngestionService(
                RepositoryExtractor(client), raw, PostgresLoader(writer)
            ),
            "commits": CommitService(
                CommitExtractor(client), raw, CommitLoader(writer)
            ),
            "issues": UpdatedEntityService(
                IssueExtractor(client), raw, IssueLoader(writer)
            ),
            "pull_requests": UpdatedEntityService(
                PullRequestExtractor(client), raw, PullRequestLoader(writer)
            ),
        }
        yield services, state


@pytest.mark.parametrize(
    "entity", ["repositories", "commits", "issues", "pull_requests"]
)
@pytest.mark.parametrize("status", [500, 429])
def test_failed_repository_does_not_block_next_and_summaries_match_audit(
    reliable_pipelines, writer, entity, status
):
    services, state = reliable_pipelines
    state.failures["/repos/octocat/broken"] = status
    service = services[entity]
    assert service.run(("octocat/broken", NAME)) == 1
    assert state.calls.count("/repos/octocat/broken") == 2
    assert len(state.waits) >= 1
    assert [row["status"] for row in service.summaries] == ["FAILED", "SUCCESS"]
    rows = writer.execute(
        "SELECT status, records_extracted, records_loaded, "
        "records_skipped, duration_ms "
        "FROM raw.pipeline_runs ORDER BY started_at"
    ).fetchall()
    assert [row[:4] for row in rows] == [("FAILED", 0, 0, 0), ("SUCCESS", 1, 1, 0)]
    assert all(row[4] >= 0 for row in rows)
    assert snapshot(writer)["pending_runs"] == []


def test_status_all_pipelines_repeated_load_and_read_only_snapshot(
    reliable_pipelines, writer, capsys
):
    services, _ = reliable_pipelines
    for service in services.values():
        assert service.run((NAME,)) == 0
    original = snapshot(writer)
    assert len(original["pipelines"]) == 4
    assert len(original["checkpoints"]) == 3
    assert all(row["consistent"] for row in original["checkpoints"])
    for service in services.values():
        assert service.run((NAME,)) == 0
    report = snapshot(writer)
    assert all(row["status"] == "SUCCESS" for row in report["pipelines"])
    assert all(row["records_loaded"] == 0 for row in report["pipelines"])
    assert report["quality"]["issue_pr_overlap"] == 0
    assert sum(row["records_skipped"] for row in report["pipelines"]) == 3
    assert snapshot(writer) == report
    print_status(report)
    assert "Repository: octocat/Hello-World" in capsys.readouterr().out
    print_status(report, as_json=True)
    assert json.loads(capsys.readouterr().out)["last_run"]["status"] == "SUCCESS"


def test_status_retains_pending_audits_even_after_success(reliable_pipelines, writer):
    services, _ = reliable_pipelines
    loader = services["repositories"].postgres
    pending = uuid4()
    loader.start_run(pending, NAME, datetime.now(UTC))
    assert services["repositories"].run((NAME,)) == 0
    report = snapshot(writer)
    assert report["last_run"]["status"] == "SUCCESS"
    assert [row["id"] for row in report["pending_runs"]] == [pending]


@pytest.mark.parametrize("entity", ["commits", "issues", "pull_requests"])
def test_corrupt_checkpoint_stops_loading_and_is_visible(
    reliable_pipelines, writer, admin, entity
):
    services, _ = reliable_pipelines
    service = services[entity]
    assert service.run((NAME,)) == 0
    before = snapshot(writer)["checkpoints"]
    # Emulate an administrative inconsistency in a previously successful audit.
    admin.execute(
        "UPDATE raw.pipeline_runs SET status='FAILED', records_loaded=0, "
        "error_message='InjectedFailure' WHERE id=%s",
        (before[0]["pipeline_run_id"],),
    )
    assert service.run((NAME,)) == 1
    assert service.summaries[0]["error_type"] == "CheckpointError"
    after = snapshot(writer)
    assert after["checkpoints"][0]["value"] == before[0]["value"]
    assert after["checkpoints"][0]["consistent"] is False
    assert after["last_run"]["records_loaded"] == 0


def test_conflicting_duplicate_commit_rolls_back(reliable_pipelines, writer):
    services, state = reliable_pipelines
    conflicting = deepcopy(state.commits[0])
    conflicting["commit"]["message"] = "Impossible content for the same SHA"
    state.commits.append(conflicting)
    assert services["commits"].run((NAME,)) == 1
    assert services["commits"].summaries[0]["error_type"] == "DataQualityError"
    assert writer.execute("SELECT count(*) FROM raw.commits").fetchone()[0] == 0
    assert snapshot(writer)["checkpoints"] == []


def test_conflicting_issue_identity_rolls_back(reliable_pipelines, writer):
    services, state = reliable_pipelines
    state.issues.append({**state.issues[0], "number": 99})
    assert services["issues"].run((NAME,)) == 1
    assert services["issues"].summaries[0]["error_type"] == "DataQualityError"
    assert writer.execute("SELECT count(*) FROM raw.issues").fetchone()[0] == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE raw.commits SET parent_shas=ARRAY['bad']",
        "UPDATE raw.commits SET parent_shas=ARRAY[NULL]::text[]",
        "UPDATE raw.commits SET committed_at='infinity'",
        "UPDATE raw.commits SET repository_id=999999",
        "UPDATE raw.issues SET updated_at=created_at-interval '1 day'",
        "UPDATE raw.issues SET title='  '",
        "UPDATE raw.issues SET state='merged'",
        "UPDATE raw.pull_requests SET merged_at=created_at-interval '1 day', "
        "state='closed'",
        "UPDATE raw.repositories SET full_name='wrong/identity'",
        "UPDATE raw.ingestion_checkpoints SET head_sha=repeat('b',40)",
        "UPDATE raw.entity_checkpoints SET watermark=updated_at+interval '1 day'",
        "UPDATE raw.pipeline_runs SET duration_ms=-1",
    ],
)
def test_database_rejects_invalid_data(reliable_pipelines, writer, statement):
    services, _ = reliable_pipelines
    for service in services.values():
        assert service.run((NAME,)) == 0
    with pytest.raises(psycopg.IntegrityError):
        with writer.transaction():
            writer.execute(statement)
    assert all(row["consistent"] for row in snapshot(writer)["checkpoints"])


def test_checkpoint_cannot_reference_unfinished_audit(reliable_pipelines, writer):
    services, _ = reliable_pipelines
    assert services["issues"].run((NAME,)) == 0
    run = uuid4()
    services["issues"].postgres.start_run(
        run, NAME, datetime.now(UTC), pipeline="issues_ingestion"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        with writer.transaction():
            writer.execute(
                "UPDATE raw.entity_checkpoints SET pipeline_run_id=%s", (run,)
            )
    assert snapshot(writer)["checkpoints"][0]["consistent"]


def test_lost_database_leaves_pending_audit_and_unknown_summary(
    reliable_pipelines, writer, admin, monkeypatch
):
    services, _ = reliable_pipelines
    service = services["issues"]
    original = service.postgres.complete

    def disconnect(*args, **kwargs):
        writer.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(service.postgres, "complete", disconnect)
    with pytest.raises(psycopg.OperationalError):
        service.run((NAME, "octocat/next"))
    assert service.summaries[0]["status"] == "UNKNOWN"
    assert service.summaries[0]["records_loaded"] is None
    assert len(snapshot(admin)["pending_runs"]) == 1
    assert admin.execute("SELECT count(*) FROM raw.issues").fetchone()[0] == 0
    assert snapshot(admin)["checkpoints"] == []


def test_status_cli_without_github_or_repositories(
    database_settings, admin, monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN")
    settings = database_settings
    for key, value in {
        "POSTGRES_HOST": settings.host,
        "POSTGRES_PORT": str(settings.port),
        "POSTGRES_DB": settings.database,
        "POSTGRES_USER": settings.admin_user,
        "GITLOG_DB_USER": settings.user,
        "GITLOG_DB_PASSWORD": settings.password.get_secret_value(),
    }.items():
        monkeypatch.setenv(key, value)
    logger = logging.getLogger("ingestion")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setattr("sys.argv", ["gitlog", "status", "--json"])
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["last_run"] is None
    assert report["pipelines"] == report["checkpoints"] == []
    print_status(report)
    assert "No runs yet." in capsys.readouterr().out


def test_future_checkpoint_is_rejected_even_with_successful_audit(
    reliable_pipelines, writer, admin
):
    services, _ = reliable_pipelines
    service = services["issues"]
    assert service.run((NAME,)) == 0
    admin.execute(
        "UPDATE raw.entity_checkpoints SET watermark=now()+interval '1 day', "
        "updated_at=now()+interval '1 day'"
    )
    assert service.run((NAME,)) == 1
    assert service.summaries[0]["error_type"] == "CheckpointError"
    report = snapshot(writer)
    assert report["checkpoints"][0]["consistent"] is False


def test_status_detects_conceptual_issue_pr_overlap(reliable_pipelines, writer, admin):
    services, _ = reliable_pipelines
    assert services["issues"].run((NAME,)) == 0
    assert services["pull_requests"].run((NAME,)) == 0
    admin.execute("UPDATE raw.issues SET number=2")
    assert snapshot(writer)["quality"]["issue_pr_overlap"] == 1
