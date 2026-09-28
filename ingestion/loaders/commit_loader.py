"""Atomically commit repository metadata, commits, checkpoint and audit outcome."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ingestion.extractors.commits import page_commits
from ingestion.loaders.postgres_loader import ConcurrentRunError, PostgresLoader
from ingestion.models.commits import Commit
from ingestion.models.github_models import Repository
from ingestion.quality import CheckpointError, DataQualityError


class ConcurrentCommitRunError(ConcurrentRunError):
    """Another connection owns this repository's commit checkpoint."""


class CommitLoader(PostgresLoader):
    lock_namespace = "commits"
    lock_error = ConcurrentCommitRunError

    def checkpoint(self, repository_id: int, branch: str) -> str | None:
        row = self.connection.execute(
            "SELECT cp.head_sha, cp.branch, r.status, r.pipeline_name, c.sha "
            "FROM raw.ingestion_checkpoints cp "
            "LEFT JOIN raw.pipeline_runs r ON r.id=cp.pipeline_run_id "
            "LEFT JOIN raw.commits c ON c.repository_id=cp.repository_id "
            "AND c.sha=cp.head_sha "
            "WHERE cp.repository_id=%s",
            (repository_id,),
        ).fetchone()
        if row is None:
            return None
        if row[2] != "SUCCESS" or row[3] != "commit_ingestion" or row[4] is None:
            raise CheckpointError("Commit checkpoint has no successful loaded history.")
        return row[0] if row[1] == branch else None

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
        seen: dict[str, str] = {}
        with self.connection.transaction():
            self.upsert_repository(repository, run_id, repository_path, extracted_at)
            for mode, path in pages:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for item in page_commits(mode, payload):
                    commit = Commit.model_validate(item)
                    fingerprint = hashlib.sha256(
                        json.dumps(
                            {
                                "commit": commit.commit.model_dump(mode="json"),
                                "parents": [parent.sha for parent in commit.parents],
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    if commit.sha in seen:
                        if seen[commit.sha] != fingerprint:
                            raise DataQualityError("Conflicting content for one SHA.")
                        continue
                    seen[commit.sha] = fingerprint
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
