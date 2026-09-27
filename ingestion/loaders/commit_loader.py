"""Atomically commit repository metadata, commits, checkpoint and audit outcome."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ingestion.extractors.commits import page_commits
from ingestion.loaders.postgres_loader import ConcurrentRunError, PostgresLoader
from ingestion.models.commits import Commit
from ingestion.models.github_models import Repository


class ConcurrentCommitRunError(ConcurrentRunError):
    """Another connection owns this repository's commit checkpoint."""


class CommitLoader(PostgresLoader):
    lock_namespace = "commits"
    lock_error = ConcurrentCommitRunError

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
                    loaded += self.upsert_changed(
                        "commits", values, ("repository_id", "sha")
                    )
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
            self.finish_run(run_id, extracted, loaded, manifest_path)
        return loaded
