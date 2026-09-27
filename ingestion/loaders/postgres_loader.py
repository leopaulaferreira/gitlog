"""One repository and its successful audit outcome commit together."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

from ingestion.models.github_models import Repository


class PostgresLoader:
    def __init__(self, connection: psycopg.Connection) -> None:
        if not connection.autocommit:
            raise ValueError(
                "Use autocommit so each explicit transaction is independent."
            )
        self.connection = connection

    def start_run(
        self,
        run_id: UUID,
        repository: str,
        started_at: datetime,
        *,
        pipeline: str = "repository_ingestion",
    ) -> None:
        self.connection.execute(
            "INSERT INTO raw.pipeline_runs "
            "(id, pipeline_name, repository, started_at, status) "
            "VALUES (%s, %s, %s, %s, 'RUNNING')",
            (run_id, pipeline, repository, started_at),
        )

    def load(
        self,
        repository: Repository,
        run_id: UUID,
        raw_path: Path,
        extracted_at: datetime,
    ) -> int:
        with self.connection.transaction():
            loaded = self.upsert_repository(repository, run_id, raw_path, extracted_at)
            updated = self.connection.execute(
                "UPDATE raw.pipeline_runs SET status = 'SUCCESS', finished_at = %s, "
                "records_extracted = 1, records_loaded = %s, raw_path = %s "
                "WHERE id = %s AND status = 'RUNNING'",
                (datetime.now(UTC), loaded, str(raw_path), run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Expected one RUNNING pipeline execution.")
        return loaded

    def upsert_repository(
        self,
        repository: Repository,
        run_id: UUID,
        raw_path: Path,
        extracted_at: datetime,
    ) -> int:
        """Participate in the caller's transaction without completing its audit."""
        values = repository.model_dump()
        values.update(
            ingested_at=extracted_at, raw_path=str(raw_path), pipeline_run_id=run_id
        )
        columns = list(values)
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(
                sql.Identifier(column), sql.Identifier(column)
            )
            for column in columns
            if column != "id"
        )
        statement = sql.SQL(
            "INSERT INTO raw.repositories ({}) VALUES ({}) "
            "ON CONFLICT (id) DO UPDATE SET {} "
            "WHERE raw.repositories.ingested_at <= EXCLUDED.ingested_at"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            assignments,
        )
        return self.connection.execute(statement, list(values.values())).rowcount

    def fail_run(
        self, run_id: UUID, extracted: int, raw_path: Path | None, error: str
    ) -> None:
        updated = self.connection.execute(
            "UPDATE raw.pipeline_runs SET status = 'FAILED', finished_at = %s, "
            "records_extracted = %s, raw_path = %s, error_message = %s "
            "WHERE id = %s AND status = 'RUNNING'",
            (
                datetime.now(UTC),
                extracted,
                str(raw_path) if raw_path else None,
                error,
                run_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Expected one RUNNING pipeline execution.")
