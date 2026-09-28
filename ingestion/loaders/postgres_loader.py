"""One repository and its successful audit outcome commit together."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

from ingestion.models.github_models import Repository


class ConcurrentRunError(Exception):
    """Another session owns this entity's repository checkpoint."""


class PostgresLoader:
    lock_namespace = "repositories"
    lock_error = ConcurrentRunError

    def __init__(self, connection: psycopg.Connection) -> None:
        if not connection.autocommit:
            raise ValueError(
                "Use autocommit so each explicit transaction is independent."
            )
        self.connection = connection
        self._started: dict[UUID, float] = {}

    def _duration_ms(self, run_id: UUID) -> int | None:
        started = self._started.get(run_id)
        return (
            max(0, int((time.monotonic() - started) * 1000))
            if started is not None
            else None
        )

    @contextmanager
    def lock(self, repository_id: int) -> Iterator[None]:
        key = f"gitlog:{self.lock_namespace}:{repository_id}"
        acquired = self.connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
        ).fetchone()[0]
        if not acquired:
            raise self.lock_error()
        try:
            yield
        finally:
            self.connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,)
            )

    def finish_run(
        self, run_id: UUID, extracted: int, loaded: int, raw_path: Path
    ) -> None:
        updated = self.connection.execute(
            "UPDATE raw.pipeline_runs SET status='SUCCESS', finished_at=%s, "
            "records_extracted=%s, records_loaded=%s, raw_path=%s "
            ", duration_ms=coalesce(%s, greatest(0, floor(extract(epoch FROM "
            "(clock_timestamp()-started_at))*1000)::bigint)) "
            "WHERE id=%s AND status='RUNNING'",
            (
                datetime.now(UTC),
                extracted,
                loaded,
                str(raw_path),
                self._duration_ms(run_id),
                run_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Expected one RUNNING pipeline execution.")

    def upsert_changed(
        self,
        table: str,
        values: dict,
        keys: tuple[str, ...],
        *,
        freshness: str | None = None,
    ) -> int:
        """Update changed source fields, preserving provenance on identical reads."""
        columns = list(values)
        mutable = [c for c in columns if c not in keys]
        source = [
            c
            for c in mutable
            if c not in ("ingested_at", "raw_path", "pipeline_run_id")
        ]
        condition = sql.SQL("({}) IS DISTINCT FROM ({})").format(
            sql.SQL(", ").join(sql.Identifier(table, c) for c in source),
            sql.SQL(", ").join(sql.Identifier("excluded", c) for c in source),
        )
        if freshness is not None:
            condition += sql.SQL(" AND {} <= {}").format(
                sql.Identifier(table, freshness), sql.Identifier("excluded", freshness)
            )
        statement = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) "
            "DO UPDATE SET {} WHERE {}"
        ).format(
            sql.Identifier("raw", table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            sql.SQL(", ").join(map(sql.Identifier, keys)),
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in mutable
            ),
            condition,
        )
        return self.connection.execute(statement, list(values.values())).rowcount

    def start_run(
        self,
        run_id: UUID,
        repository: str,
        started_at: datetime,
        *,
        pipeline: str = "repository_ingestion",
    ) -> None:
        self._started[run_id] = time.monotonic()
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
            self.finish_run(run_id, 1, loaded, raw_path)
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
        loaded = self.upsert_changed(
            "repositories", values, ("id",), freshness="ingested_at"
        )
        if not loaded:
            # Even an identical observation must prevent an older in-flight
            # response from replacing it. Preserve the source-change provenance.
            self.connection.execute(
                "UPDATE raw.repositories SET ingested_at=GREATEST(ingested_at,%s) "
                "WHERE id=%s",
                (extracted_at, repository.id),
            )
        return loaded

    def fail_run(
        self, run_id: UUID, extracted: int, raw_path: Path | None, error: str
    ) -> None:
        updated = self.connection.execute(
            "UPDATE raw.pipeline_runs SET status = 'FAILED', finished_at = %s, "
            "records_extracted = %s, raw_path = %s, error_message = %s, "
            "duration_ms=coalesce(%s, greatest(0, floor(extract(epoch FROM "
            "(clock_timestamp()-started_at))*1000)::bigint)) "
            "WHERE id = %s AND status = 'RUNNING'",
            (
                datetime.now(UTC),
                extracted,
                str(raw_path) if raw_path else None,
                error,
                self._duration_ms(run_id),
                run_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Expected one RUNNING pipeline execution.")
