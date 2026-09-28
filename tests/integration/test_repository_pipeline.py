"""Real PostgreSQL transactions and constraints; GitHub remains mocked."""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import psycopg
import pytest

from ingestion.client import GitHubClient
from ingestion.config import DatabaseSettings
from ingestion.db.migrate import MigrationError, migrate
from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.loaders.raw_loader import RawLoader
from ingestion.main import main
from ingestion.models.github_models import Repository
from ingestion.services.ingestion_service import IngestionService

pytestmark = pytest.mark.integration


def test_two_runs_update_attributes_without_duplicate_repositories(
    writer: psycopg.Connection, repository_payload: dict, tmp_path: Path
) -> None:
    with GitHubClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=repository_payload)
        )
    ) as client:
        service = IngestionService(
            RepositoryExtractor(client), RawLoader(tmp_path), PostgresLoader(writer)
        )
        assert service.run(("octocat/Hello-World",)) == 0
        assert (
            writer.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 1
        )
        repository_payload["stargazers_count"] = 99
        repository_payload["description"] = "Quote ' ; DROP TABLE raw.repositories; --"
        assert service.run(("octocat/Hello-World",)) == 0
    assert writer.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 1
    row = writer.execute(
        "SELECT stars, description, watchers FROM raw.repositories"
    ).fetchone()
    assert row == (99, repository_payload["description"], 7)
    runs = writer.execute(
        "SELECT status, records_extracted, records_loaded, finished_at, raw_path "
        "FROM raw.pipeline_runs"
    ).fetchall()
    assert len(runs) == 2
    assert all(row[:3] == ("SUCCESS", 1, 1) and row[3] is not None for row in runs)
    assert len(list(tmp_path.rglob("*.json"))) == 2
    snapshots = [json.loads(Path(row[4]).read_text()) for row in runs]
    assert {snapshot["stargazers_count"] for snapshot in snapshots} == {80, 99}
    assert all("future_field" in snapshot for snapshot in snapshots)


def test_failure_is_audited_and_next_repository_continues(
    writer: psycopg.Connection, repository_payload: dict, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("missing"):
            return httpx.Response(404, json={"message": "sensitive"})
        return httpx.Response(200, json=repository_payload)

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        service = IngestionService(
            RepositoryExtractor(client), RawLoader(tmp_path), PostgresLoader(writer)
        )
        assert service.run(("octocat/missing", "octocat/Hello-World")) == 1
    rows = writer.execute(
        "SELECT status, records_extracted, records_loaded, error_message "
        "FROM raw.pipeline_runs ORDER BY started_at"
    ).fetchall()
    assert rows == [("FAILED", 0, 0, "GitHubNotFoundError"), ("SUCCESS", 1, 1, None)]
    assert writer.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 1


def test_invalid_model_is_preserved_and_failure_is_audited(
    writer: psycopg.Connection, tmp_path: Path
) -> None:
    service = IngestionService(
        Mock(extract=Mock(return_value={"id": "bad"})),
        RawLoader(tmp_path),
        PostgresLoader(writer),
    )
    assert service.run(("a/b",)) == 1
    row = writer.execute(
        "SELECT status, records_extracted, records_loaded, raw_path, error_message "
        "FROM raw.pipeline_runs"
    ).fetchone()
    assert row[:3] == ("FAILED", 1, 0)
    assert row[4] == "ValidationError"
    assert json.loads(Path(row[3]).read_text()) == {"id": "bad"}
    assert writer.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 0


def test_upsert_rolls_back_if_success_audit_fails(
    writer: psycopg.Connection, repository_payload: dict, tmp_path: Path
) -> None:
    loader = PostgresLoader(writer)
    run_id = uuid4()
    loader.start_run(run_id, "octocat/Hello-World", datetime.now(UTC))
    loader.fail_run(run_id, 0, None, "TestFailure")
    with pytest.raises(ValueError, match="RUNNING"):
        loader.load(
            Repository.model_validate(repository_payload),
            run_id,
            tmp_path / "raw.json",
            datetime.now(UTC),
        )
    assert writer.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 0
    assert (
        writer.execute("SELECT status FROM raw.pipeline_runs").fetchone()[0] == "FAILED"
    )


def test_sql_failure_rolls_back_and_is_audited(
    writer: psycopg.Connection, repository_payload: dict, tmp_path: Path
) -> None:
    # PostgreSQL text cannot contain NUL; raw JSON must survive the load failure.
    repository_payload["description"] = "invalid\u0000value"
    service = IngestionService(
        Mock(extract=Mock(return_value=repository_payload)),
        RawLoader(tmp_path),
        PostgresLoader(writer),
    )
    assert service.run(("octocat/Hello-World",)) == 1
    row = writer.execute(
        "SELECT status, records_loaded, raw_path FROM raw.pipeline_runs"
    ).fetchone()
    assert row[:2] == ("FAILED", 0)
    assert Path(row[2]).exists()
    assert writer.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 0


def test_stale_snapshot_cannot_overwrite_newer_load(
    writer: psycopg.Connection, repository_payload: dict, tmp_path: Path
) -> None:
    loader = PostgresLoader(writer)
    now = datetime.now(UTC)
    model = Repository.model_validate(repository_payload)
    new_run, old_run = uuid4(), uuid4()
    for run in (new_run, old_run):
        loader.start_run(run, model.full_name, now)
    assert loader.load(model, new_run, tmp_path / "new.json", now) == 1
    assert (
        loader.load(
            model.model_copy(update={"stars": 1}),
            old_run,
            tmp_path / "old.json",
            now - timedelta(seconds=10),
        )
        == 0
    )
    assert (
        writer.execute("SELECT stars FROM raw.repositories").fetchone()[0]
        == model.stars
    )
    assert (
        writer.execute(
            "SELECT records_loaded FROM raw.pipeline_runs WHERE id=%s", (old_run,)
        ).fetchone()[0]
        == 0
    )


def test_renamed_repository_uses_stable_id(
    writer: psycopg.Connection, repository_payload: dict, tmp_path: Path
) -> None:
    service = IngestionService(
        Mock(extract=Mock(side_effect=lambda _: repository_payload)),
        RawLoader(tmp_path),
        PostgresLoader(writer),
    )
    assert service.run(("octocat/Hello-World",)) == 0
    repository_payload.update(name="renamed", full_name="octocat/renamed")
    assert service.run(("octocat/renamed",)) == 0
    assert writer.execute("SELECT full_name FROM raw.repositories").fetchall() == [
        ("octocat/renamed",)
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE raw.forbidden(id int)",
        "DELETE FROM raw.repositories",
        "TRUNCATE raw.repositories",
        "UPDATE public.gitlog_schema_migrations SET checksum='bad'",
    ],
)
def test_ingestion_role_cannot_administer_database(
    writer: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer.execute(statement)


def test_migrations_are_repeatable_and_checksum_verified(
    admin: psycopg.Connection, database_settings: DatabaseSettings
) -> None:
    migrate(admin, database_settings)
    migrate(admin, database_settings)
    assert (
        admin.execute(
            "SELECT count(*) FROM public.gitlog_schema_migrations"
        ).fetchone()[0]
        == 4
    )
    with pytest.raises(MigrationError, match="modified"):
        with admin.transaction():
            admin.execute(
                "UPDATE public.gitlog_schema_migrations SET checksum='modified'"
            )
            migrate(admin, database_settings)
    assert (
        admin.execute(
            "SELECT checksum FROM public.gitlog_schema_migrations"
        ).fetchone()[0]
        != "modified"
    )


def test_unknown_migration_is_rejected(
    admin: psycopg.Connection, database_settings: DatabaseSettings
) -> None:
    with pytest.raises(MigrationError, match="Unknown"):
        with admin.transaction():
            admin.execute(
                "INSERT INTO public.gitlog_schema_migrations "
                "VALUES ('999_unknown.sql', 'bad', now())"
            )
            migrate(admin, database_settings)


def test_invalid_audit_status_is_rejected(writer: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        writer.execute(
            "INSERT INTO raw.pipeline_runs "
            "(id, pipeline_name, repository, started_at, status) "
            "VALUES (%s, 'test', 'a/b', now(), 'INVALID')",
            (uuid4(),),
        )


def test_cli_runs_migrations_and_repository_pipeline(
    admin: psycopg.Connection,
    database_settings: DatabaseSettings,
    repository_payload: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    settings = database_settings
    monkeypatch.chdir(tmp_path)
    env = {
        "POSTGRES_HOST": settings.host,
        "POSTGRES_PORT": str(settings.port),
        "POSTGRES_DB": settings.database,
        "POSTGRES_USER": settings.admin_user,
        "POSTGRES_PASSWORD": settings.admin_password.get_secret_value(),
        "GITLOG_DB_USER": settings.user,
        "GITLOG_DB_PASSWORD": settings.password.get_secret_value(),
        "GITLOG_REPOSITORIES": "octocat/Hello-World,OCTOCAT/hello-world",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # Restore logger configuration after the CLI so caplog remains usable.
    logger = logging.getLogger("ingestion")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setattr("sys.argv", ["gitlog", "migrate"])
    assert main() == 0
    monkeypatch.setattr(
        "ingestion.main.GitHubClient",
        lambda: GitHubClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=repository_payload)
            )
        ),
    )
    monkeypatch.setattr("sys.argv", ["gitlog", "repositories"])
    assert main() == 0
    assert main() == 0
    assert admin.execute("SELECT count(*) FROM raw.repositories").fetchone()[0] == 1
    assert (
        admin.execute(
            "SELECT count(*) FROM raw.pipeline_runs WHERE status='SUCCESS'"
        ).fetchone()[0]
        == 2
    )
    documents = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert sum(doc["event"] == "ingestion.completed" for doc in documents) == 2
    assert settings.password.get_secret_value() not in json.dumps(documents)


def test_identical_observation_still_rejects_older_inflight_response(
    writer, repository_payload, tmp_path
):
    loader = PostgresLoader(writer)
    model = Repository.model_validate(repository_payload)
    now = datetime.now(UTC)
    runs = [uuid4() for _ in range(3)]
    for run in runs:
        loader.start_run(run, model.full_name, now)
    assert loader.load(model, runs[0], tmp_path / "first.json", now) == 1
    assert (
        loader.load(model, runs[1], tmp_path / "same.json", now + timedelta(seconds=20))
        == 0
    )
    assert (
        loader.load(
            model.model_copy(update={"stars": 1}),
            runs[2],
            tmp_path / "stale.json",
            now + timedelta(seconds=10),
        )
        == 0
    )
    assert writer.execute(
        "SELECT stars, pipeline_run_id, ingested_at FROM raw.repositories"
    ).fetchone() == (model.stars, runs[0], now + timedelta(seconds=20))
    assert (
        writer.execute(
            "SELECT records_skipped FROM raw.pipeline_runs ORDER BY started_at, id"
        )
        .fetchall()
        .count((1,))
        == 2
    )
