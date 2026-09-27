"""Shared raw-first, incremental ingestion of issues and hydrated pull requests."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import TypeAdapter

from ingestion.client.exceptions import GitHubResponseError
from ingestion.extractors.issues import IssueExtractor
from ingestion.extractors.pull_requests import PullRequestExtractor
from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.raw_loader import RawLoader
from ingestion.loaders.updated_entity_loader import UpdatedEntityLoader
from ingestion.models.github_models import Identifier, Repository

logger = logging.getLogger(__name__)
number_adapter = TypeAdapter(Identifier)


class UpdatedEntityService:
    def __init__(
        self,
        extractor: IssueExtractor,
        raw: RawLoader,
        postgres: UpdatedEntityLoader,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if extractor.entity != postgres.entity:
            raise ValueError("Extractor and loader entities must agree.")
        self.extractor, self.raw, self.postgres, self.clock = (
            extractor,
            raw,
            postgres,
            clock,
        )

    def run(self, repositories: tuple[str, ...], *, full_refresh: bool = False) -> int:
        failures = 0
        entity = self.extractor.entity
        for name in repositories:
            run_id, started, extracted_at = uuid4(), time.monotonic(), self.clock()
            context = {"entity": entity, "repository": name, "run_id": str(run_id)}
            self.postgres.start_run(
                run_id, name, extracted_at, pipeline=f"{entity}_ingestion"
            )
            logger.info("entity.start", extra=context)
            extracted = 0
            raw_path = None
            try:
                payload = RepositoryExtractor(self.extractor.client).extract(name)
                repository_path = self.raw.save_document(
                    entity, name, run_id, "repository", payload, extracted_at
                )
                raw_path = repository_path.parent
                repository = Repository.model_validate(payload)
                with self.postgres.lock(repository.id):
                    # Bound progress by the start of discovery, never by the
                    # newest item or the time the last page happened to finish.
                    watermark = self.clock()
                    checkpoint = self.postgres.checkpoint(repository.id)
                    since = (
                        None
                        if full_refresh or checkpoint is None
                        else (checkpoint - timedelta(minutes=5))
                    )
                    pages, documents = [], []
                    numbers: set[int] = set()
                    for page in self.extractor.pages(repository.full_name, since):
                        # Count only this entity; keep the entire mixed response raw.
                        if isinstance(page, list):
                            extracted += sum(
                                isinstance(item, dict)
                                and ("pull_request" in item)
                                == (entity == "pull_requests")
                                for item in page
                            )
                        path = self.raw.save_document(
                            entity,
                            name,
                            run_id,
                            f"page-{len(pages) + 1:06d}",
                            page,
                            extracted_at,
                        )
                        pages.append(path)
                        for item in self.extractor.selected(page):
                            if isinstance(self.extractor, PullRequestExtractor):
                                numbers.add(
                                    number_adapter.validate_python(item.get("number"))
                                )
                            else:
                                self.extractor.model.model_validate(item)
                        logger.info(
                            "entity.page_saved",
                            extra={
                                **context,
                                "page": len(pages),
                                "records_extracted": extracted,
                            },
                        )
                    if isinstance(self.extractor, PullRequestExtractor):
                        for number in sorted(numbers):
                            detail = self.extractor.detail(repository.full_name, number)
                            path = self.raw.save_document(
                                entity,
                                name,
                                run_id,
                                f"pull-request-{number}",
                                detail,
                                extracted_at,
                            )
                            documents.append(path)
                            model = self.extractor.model.model_validate(detail)
                            if model.number != number:
                                raise GitHubResponseError(
                                    "Unexpected pull request number."
                                )
                    else:
                        documents = pages
                    manifest = self.raw.save_document(
                        entity,
                        name,
                        run_id,
                        "manifest",
                        {
                            "entity": entity,
                            "repository_id": repository.id,
                            "since": since.isoformat() if since else None,
                            "watermark": watermark.isoformat(),
                            "records_extracted": extracted,
                            "pages": [str(p) for p in pages],
                            "documents": [str(p) for p in documents],
                        },
                        extracted_at,
                    )
                    loaded = self.postgres.complete(
                        repository,
                        run_id,
                        repository_path,
                        manifest,
                        documents,
                        watermark,
                        extracted,
                        extracted_at,
                    )
            except Exception as error:
                try:
                    self.postgres.fail_run(
                        run_id, extracted, raw_path, type(error).__name__
                    )
                except Exception:
                    logger.error("entity.audit_failed", extra=context)
                    raise
                logger.error(
                    "entity.failed",
                    extra={
                        **context,
                        "records_extracted": extracted,
                        "records_loaded": 0,
                        "error_type": type(error).__name__,
                        "duration": time.monotonic() - started,
                    },
                )
                failures += 1
                continue
            logger.info(
                "entity.completed",
                extra={
                    **context,
                    "records_extracted": extracted,
                    "records_loaded": loaded,
                    "duration": time.monotonic() - started,
                },
            )
        return failures
