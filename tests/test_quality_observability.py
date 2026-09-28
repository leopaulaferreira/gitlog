"""Critical validation and failures that do not require a running database."""

import json
import logging
import socket
from unittest.mock import Mock

import psycopg
import pytest
from pydantic import ValidationError

from ingestion.logging_config import JsonFormatter
from ingestion.main import main
from ingestion.models.github_models import Repository
from ingestion.models.issues import Issue, PullRequest
from ingestion.observability import run_context
from ingestion.services.ingestion_service import IngestionService


@pytest.mark.parametrize(
    "field,value",
    [
        ("updated_at", "2000-01-01T00:00:00Z"),
        ("node_id", "  "),
        ("default_branch", "\t"),
    ],
)
def test_invalid_repository_quality(repository_payload, field, value):
    repository_payload[field] = value
    with pytest.raises(ValidationError):
        Repository.model_validate(repository_payload)


@pytest.mark.parametrize(
    "model,fixture", [(Issue, "issue_payload"), (PullRequest, "pull_request_payload")]
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("updated_at", "2000-01-01T00:00:00Z"),
        ("closed_at", "2000-01-01T00:00:00Z"),
        ("title", "  "),
        ("user", {"login": "  "}),
        ("updated_at", "infinity"),
        ("created_at", "2026-09-01T00:00:00"),
    ],
)
def test_invalid_issue_and_pr_quality(request, model, fixture, field, value):
    payload = request.getfixturevalue(fixture)
    payload[field] = value
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_merge_before_creation_is_rejected(pull_request_payload):
    pull_request_payload.update(state="closed", merged_at="2000-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        PullRequest.model_validate(pull_request_payload)


def test_http_log_inherits_run_context_without_payload_or_secret():
    token = run_context.set(
        {
            "repository": "a/b",
            "entity": "commits",
            "run_id": "123",
            "password": "private",
        }
    )
    try:
        record = logging.LogRecord(
            "ingestion.client", logging.INFO, __file__, 1, "github.retry", (), None
        )
        record.delay_seconds = 2
        record.payload = {"secret": "private"}
        document = json.loads(JsonFormatter().format(record))
        assert document["run_id"] == "123"
        assert document["repository"] == "a/b"
        assert document["entity"] == "commits"
        assert document["delay_seconds"] == 2
        assert "private" not in json.dumps(document)
    finally:
        run_context.reset(token)


def test_audit_start_failure_stops_before_http_and_resets_context():
    extractor, postgres = Mock(), Mock()
    postgres.start_run.side_effect = psycopg.OperationalError("sensitive")
    service = IngestionService(extractor, Mock(), postgres)
    with pytest.raises(psycopg.OperationalError):
        service.run(("a/b", "a/c"))
    extractor.extract.assert_not_called()
    postgres.fail_run.assert_not_called()
    assert run_context.get() is None


def test_database_unavailable_cli_has_safe_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("GITLOG_DB_PASSWORD", "secret-must-not-appear")
    logger = logging.getLogger("ingestion")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setattr("sys.argv", ["gitlog", "status"])
    # Reserve a loopback port without a listener: a real connection is refused.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        monkeypatch.setenv("POSTGRES_PORT", str(reserved.getsockname()[1]))
        assert main() == 1
    captured = capsys.readouterr()
    event = json.loads(captured.err)
    assert event["event"] == "command.failed"
    assert event["error_type"] == "OperationalError"
    assert "secret-must-not-appear" not in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
