"""Repository configuration, validation, raw preservation and service behavior."""

import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from ingestion.client import GitHubClient
from ingestion.config import DatabaseSettings, IngestionSettings
from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.raw_loader import RawLoader
from ingestion.logging_config import JsonFormatter
from ingestion.models.github_models import Repository
from ingestion.services.ingestion_service import IngestionService


def test_configuration_normalizes_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GITLOG_REPOSITORIES", "octocat/Hello-World, OCTOCAT/hello-world, x/y"
    )
    assert IngestionSettings.from_env().repositories == ("octocat/Hello-World", "x/y")


@pytest.mark.parametrize(
    "value", ["", "a", "../x", "a/..", "a/b/c", "a/b?token=x", "a/b,", "a/%2e"]
)
def test_invalid_repository_configuration(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITLOG_REPOSITORIES", value)
    with pytest.raises(ValidationError):
        IngestionSettings.from_env()


def test_database_settings_separate_roles_and_hide_passwords() -> None:
    settings = DatabaseSettings(password="secret", admin_password="admin-secret")
    assert "admin-secret" not in repr(settings)
    assert "'secret'" not in repr(settings)
    assert settings.connection_kwargs()["user"] == "gitlog_ingestion"
    assert settings.connection_kwargs(admin=True)["user"] == "gitlog"
    for kwargs in (
        {"user": "gitlog"},
        {"user": "pg_admin"},
        {"port": 0},
        {"password": ""},
    ):
        with pytest.raises(ValidationError):
            DatabaseSettings.model_validate({"password": "test", **kwargs})


def test_model_maps_source_fields_without_mutation(repository_payload: dict) -> None:
    original = json.dumps(repository_payload)
    repository = Repository.model_validate(repository_payload)
    assert repository.owner == "octocat"
    assert repository.stars == 80
    assert repository.watchers == 7
    assert repository.created_at.utcoffset() == timedelta(0)
    assert "future_field" not in repository.model_dump()
    assert json.dumps(repository_payload) == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 0),
        ("id", True),
        ("id", "12"),
        ("stargazers_count", -1),
        ("forks_count", 1.5),
        ("created_at", "2026-01-01T00:00:00"),
        ("visibility", "invalid"),
        ("archived", "false"),
        ("owner", None),
        ("full_name", "different/name"),
        ("full_name", "octocat/.."),
    ],
)
def test_model_rejects_invalid_data(
    repository_payload: dict, field: str, value: object
) -> None:
    repository_payload[field] = value
    with pytest.raises(ValidationError):
        Repository.model_validate(repository_payload)


def test_nullable_metadata_and_missing_required_field(repository_payload: dict) -> None:
    repository_payload.update(description=None, language=None, pushed_at=None)
    assert Repository.model_validate(repository_payload).pushed_at is None
    del repository_payload["node_id"]
    with pytest.raises(ValidationError):
        Repository.model_validate(repository_payload)


def test_extractor_uses_client_and_keeps_unmodeled_fields(
    repository_payload: dict,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/Hello-World"
        return httpx.Response(200, json=repository_payload)

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            RepositoryExtractor(client).extract("octocat/Hello-World")
            == repository_payload
        )


def test_raw_preserves_payload_uses_utc_and_does_not_overwrite(
    tmp_path: Path, repository_payload: dict
) -> None:
    raw = RawLoader(tmp_path)
    run_id = uuid4()
    # UTC date is the next day.
    instant = datetime(2026, 9, 26, 23, tzinfo=timezone(timedelta(hours=-3)))
    path = raw.save("octocat/Hello-World", run_id, repository_payload, instant)
    assert "year=2026/month=09/day=27" in str(path)
    assert json.loads(path.read_text()) == repository_payload
    with pytest.raises(FileExistsError):
        raw.save("octocat/Hello-World", run_id, {}, instant)
    assert json.loads(path.read_text()) == repository_payload
    assert not list(tmp_path.rglob("*.tmp"))


def test_raw_rejects_unsafe_path_and_naive_time(tmp_path: Path) -> None:
    for name, instant in [("a/..", datetime.now(UTC)), ("a/b", datetime(2026, 1, 1))]:
        with pytest.raises(ValueError):
            RawLoader(tmp_path).save(name, uuid4(), {}, instant)
    assert not list(tmp_path.iterdir())


def test_raw_partial_write_never_publishes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise OSError("disk error")

    monkeypatch.setattr("ingestion.loaders.raw_loader.os.fsync", fail)
    with pytest.raises(OSError):
        RawLoader(tmp_path).save("a/b", uuid4(), {}, datetime.now(UTC))
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_service_preserves_invalid_payload_before_validation(tmp_path: Path) -> None:
    payload = {"invalid": "sensitive response"}
    postgres = Mock()
    service = IngestionService(
        Mock(extract=Mock(return_value=payload)), RawLoader(tmp_path), postgres
    )
    assert service.run(("a/b",)) == 1
    assert json.loads(next(tmp_path.rglob("*.json")).read_text()) == payload
    postgres.load.assert_not_called()
    args = postgres.fail_run.call_args.args
    assert args[1] == 1
    assert args[3] == "ValidationError"
    assert "sensitive" not in str(args)


def test_service_does_not_call_api_if_audit_cannot_start(tmp_path: Path) -> None:
    extractor = Mock()
    postgres = Mock(start_run=Mock(side_effect=OSError("database unavailable")))
    with pytest.raises(OSError):
        IngestionService(extractor, RawLoader(tmp_path), postgres).run(("a/b",))
    extractor.extract.assert_not_called()


def test_service_raw_failure_prevents_database_load(repository_payload: dict) -> None:
    postgres = Mock()
    service = IngestionService(
        Mock(extract=Mock(return_value=repository_payload)),
        Mock(save=Mock(side_effect=OSError("disk full"))),
        postgres,
    )
    assert service.run(("octocat/Hello-World",)) == 1
    postgres.load.assert_not_called()
    assert postgres.fail_run.call_args.args[1:] == (1, None, "OSError")


def test_service_aborts_if_failure_cannot_be_audited(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    postgres = Mock(fail_run=Mock(side_effect=OSError("db unavailable")))
    with pytest.raises(OSError):
        IngestionService(
            Mock(extract=Mock(side_effect=ValueError("private"))),
            RawLoader(tmp_path),
            postgres,
        ).run(("a/b",))
    assert "ingestion.audit_failed" in caplog.text
    assert "private" not in caplog.text


def test_json_logger_allows_only_operational_fields() -> None:
    record = logging.LogRecord(
        "ingestion.test", logging.INFO, __file__, 1, "ingestion.start", (), None
    )
    record.repository = "a/b"
    record.run_id = "run-123"
    record.password = "do-not-log"
    document = json.loads(JsonFormatter().format(record))
    assert document["event"] == "ingestion.start"
    assert document["repository"] == "a/b"
    assert "password" not in document
    assert document["timestamp"].endswith("+00:00")
