"""Repository pipeline: audit → extract → raw → validate → atomic load."""

import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.loaders.raw_loader import RawLoader
from ingestion.models.github_models import Repository

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(
        self, extractor: RepositoryExtractor, raw: RawLoader, postgres: PostgresLoader
    ) -> None:
        self.extractor = extractor
        self.raw = raw
        self.postgres = postgres

    def run(self, repositories: tuple[str, ...]) -> int:
        """Continue after an individual failure; return the failed repository count."""
        failures = 0
        for repository in repositories:
            run_id = uuid4()
            started = time.monotonic()
            context = {"repository": repository, "run_id": str(run_id)}
            # If audit cannot start, stop before calling the API.
            self.postgres.start_run(run_id, repository, datetime.now(UTC))
            logger.info("ingestion.start", extra=context)
            extracted = 0
            raw_path = None
            try:
                payload = self.extractor.extract(repository)
                extracted_at = datetime.now(UTC)
                extracted = 1
                logger.info(
                    "ingestion.records_received", extra={**context, "records": 1}
                )
                raw_path = self.raw.save(repository, run_id, payload, extracted_at)
                logger.info("raw.saved", extra=context)
                model = Repository.model_validate(payload)
                loaded = self.postgres.load(model, run_id, raw_path, extracted_at)
            except Exception as error:
                # Validation and SQL exception bodies may contain secrets.
                category = type(error).__name__
                try:
                    self.postgres.fail_run(run_id, extracted, raw_path, category)
                except Exception:
                    logger.error("ingestion.audit_failed", extra=context)
                    raise
                logger.error(
                    "ingestion.failed",
                    extra={
                        **context,
                        "error_type": category,
                        "duration": time.monotonic() - started,
                    },
                )
                failures += 1
                continue
            logger.info("postgres.upsert", extra={**context, "records": loaded})
            logger.info(
                "ingestion.completed",
                extra={
                    **context,
                    "records": loaded,
                    "duration": time.monotonic() - started,
                },
            )
        return failures
