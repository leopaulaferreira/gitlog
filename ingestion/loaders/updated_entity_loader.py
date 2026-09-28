"""Shared atomic UPSERT/checkpoint loading for mutable issues and pull requests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from ingestion.extractors.issues import issue_page
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.models.github_models import Repository
from ingestion.models.issues import Issue, IssueFields, PullRequest
from ingestion.quality import CheckpointError, DataQualityError


class UpdatedEntityLoader(PostgresLoader):
    entity: str
    model: type[IssueFields]

    def checkpoint(self, repository_id: int) -> datetime | None:
        row = self.connection.execute(
            "SELECT cp.watermark, r.status, r.pipeline_name "
            "FROM raw.entity_checkpoints cp LEFT JOIN raw.pipeline_runs r "
            "ON r.id=cp.pipeline_run_id WHERE cp.repository_id=%s AND cp.entity=%s",
            (repository_id, self.entity),
        ).fetchone()
        if row is None:
            return None
        if (
            row[1] != "SUCCESS"
            or row[2] != f"{self.entity}_ingestion"
            or row[0] > datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise CheckpointError("Inconsistent entity checkpoint.")
        return row[0]

    def complete(
        self,
        repository: Repository,
        run_id: UUID,
        repository_path: Path,
        manifest_path: Path,
        documents: list[Path],
        watermark: datetime,
        extracted: int,
        extracted_at: datetime,
    ) -> int:
        # Keep only typed source fields, not large bodies or original envelopes.
        # Pick the newest occurrence when mutable pagination repeats an item.
        selected: dict[int, tuple[IssueFields, Path]] = {}
        for path in documents:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = (
                issue_page(payload, pull_requests=False)
                if self.entity == "issues"
                else [payload]
            )
            for item in items:
                model = self.model.model_validate(item)
                old = selected.get(model.id)
                if old is not None and old[0].number != model.number:
                    raise DataQualityError("One entity ID has conflicting numbers.")
                if old is None or old[0].updated_at <= model.updated_at:
                    selected[model.id] = model, path
        loaded = 0
        with self.connection.transaction():
            self.upsert_repository(repository, run_id, repository_path, extracted_at)
            for model, path in selected.values():
                values = model.model_dump()
                values.update(
                    repository_id=repository.id,
                    raw_path=str(path),
                    ingested_at=extracted_at,
                    pipeline_run_id=run_id,
                )
                loaded += self.upsert_changed(
                    self.entity, values, ("id",), freshness="updated_at"
                )
            self.connection.execute(
                "INSERT INTO raw.entity_checkpoints "
                "(repository_id, entity, watermark, updated_at, pipeline_run_id) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (repository_id, entity) "
                "DO UPDATE SET watermark=GREATEST(raw.entity_checkpoints.watermark, "
                "EXCLUDED.watermark), updated_at=EXCLUDED.updated_at, "
                "pipeline_run_id=EXCLUDED.pipeline_run_id",
                (repository.id, self.entity, watermark, datetime.now(UTC), run_id),
            )
            self.finish_run(run_id, extracted, loaded, manifest_path)
        return loaded


class IssueLoader(UpdatedEntityLoader):
    entity = lock_namespace = "issues"
    model = Issue


class PullRequestLoader(UpdatedEntityLoader):
    entity = lock_namespace = "pull_requests"
    model = PullRequest
