"""Atomically commit repository metadata, commits, checkpoint and audit outcome."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from psycopg import sql

from ingestion.extractors.commits import page_commits
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.models.commits import Commit
from ingestion.models.github_models import Repository


class ConcurrentCommitRunError(Exception):
    """Another connection owns this repository's commit checkpoint."""


class CommitLoader(PostgresLoader):
    @contextmanager
    def lock(self, repository_id: int) -> Iterator[None]:
        key = f"gitlog:commits:{repository_id}"
        acquired = self.connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
        ).fetchone()[0]
        if not acquired:
            raise ConcurrentCommitRunError()
        try:
            yield
        finally:
            self.connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,)
            )

    def checkpoint(self, repository_id: int, branch: str) -> str | None:
        row = self.connection.execute(
            "SELECT head_sha FROM raw.ingestion_checkpoints "
            "WHERE repository_id = %s AND branch = %s",
            (repository_id, branch),
        ).fetchone()
        return row[0] if row else None

    def complete(
        self,
        repository: Repository,
        run_id: UUID,
        repository_path: Path,
        manifest_path: Path,
        pages: list[tuple[str, Path]],
        head: str | None,
        extracted: int,
        extracted_at: datetime,
    ) -> int:
        loaded = 0
        seen: set[str] = set()
        with self.connection.transaction():
            self.upsert_repository(repository, run_id, repository_path, extracted_at)
            for mode, path in pages:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for item in page_commits(mode, payload):
                    commit = Commit.model_validate(item)
                    if commit.sha in seen:
                        continue
                    seen.add(commit.sha)
                    values = commit.database_values()
                    values.update(
                        repository_id=repository.id,
                        ingested_at=extracted_at,
                        raw_path=str(path),
                        pipeline_run_id=run_id,
                    )
                    columns = list(values)
                    mutable = [c for c in columns if c not in ("repository_id", "sha")]
                    # Only changed source fields count as loaded. Re-reading an
                    # identical commit must not rewrite its provenance or metrics.
                    source = [
                        c
                        for c in mutable
                        if c not in ("ingested_at", "raw_path", "pipeline_run_id")
                    ]
                    statement = sql.SQL(
                        "INSERT INTO raw.commits ({}) VALUES ({}) "
                        "ON CONFLICT (repository_id, sha) DO UPDATE SET {} "
                        "WHERE ({}) IS DISTINCT FROM ({})"
                    ).format(
                        sql.SQL(", ").join(map(sql.Identifier, columns)),
                        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                        sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(
                                sql.Identifier(c), sql.Identifier(c)
                            )
                            for c in mutable
                        ),
                        sql.SQL(", ").join(
                            sql.Identifier("commits", c) for c in source
                        ),
                        sql.SQL(", ").join(
                            sql.Identifier("excluded", c) for c in source
                        ),
                    )
                    loaded += self.connection.execute(
                        statement, list(values.values())
                    ).rowcount
            if head is not None:
                self.connection.execute(
                    "INSERT INTO raw.ingestion_checkpoints "
                    "(repository_id, branch, head_sha, updated_at, pipeline_run_id) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (repository_id) DO UPDATE SET "
                    "branch=EXCLUDED.branch, head_sha=EXCLUDED.head_sha, "
                    "updated_at=EXCLUDED.updated_at, "
                    "pipeline_run_id=EXCLUDED.pipeline_run_id",
                    (
                        repository.id,
                        repository.default_branch,
                        head,
                        datetime.now(UTC),
                        run_id,
                    ),
                )
            updated = self.connection.execute(
                "UPDATE raw.pipeline_runs SET status='SUCCESS', finished_at=%s, "
                "records_extracted=%s, records_loaded=%s, raw_path=%s "
                "WHERE id=%s AND status='RUNNING'",
                (datetime.now(UTC), extracted, loaded, str(manifest_path), run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Expected one RUNNING pipeline execution.")
        return loaded
