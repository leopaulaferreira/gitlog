"""Preserve every page before validation and advance checkpoints only on success."""

import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from ingestion.client.exceptions import GitHubHTTPError, GitHubPaginationError
from ingestion.extractors.commits import CommitExtractor, page_commits
from ingestion.loaders.commit_loader import CommitLoader
from ingestion.loaders.raw_loader import RawLoader
from ingestion.models.commits import Commit, Reference
from ingestion.models.github_models import Repository
from ingestion.observability import run_context, scoped_run, summarize

logger = logging.getLogger(__name__)


class CommitService:
    def __init__(
        self, extractor: CommitExtractor, raw: RawLoader, postgres: CommitLoader
    ) -> None:
        self.extractor, self.raw, self.postgres = extractor, raw, postgres

    @scoped_run
    def run(self, repositories: tuple[str, ...], *, full_refresh: bool = False) -> int:
        failures = 0
        for name in repositories:
            run_id = uuid4()
            started = time.monotonic()
            extracted_at = datetime.now(UTC)
            context = {"repository": name, "entity": "commits", "run_id": str(run_id)}
            run_context.set(context)
            self.postgres.start_run(
                run_id, name, extracted_at, pipeline="commit_ingestion"
            )
            logger.info("commits.start", extra=context)
            extracted = 0
            raw_path = None
            try:
                payload = self.extractor.repository(name)
                repository_path = self.raw.save_commit_document(
                    name, run_id, "repository", payload, extracted_at
                )
                raw_path = repository_path.parent
                repository = Repository.model_validate(payload)
                # The stable numeric ID also serializes aliases and renames.
                with self.postgres.lock(repository.id):
                    head = None
                    try:
                        reference_payload = self.extractor.reference(
                            repository.full_name, repository.default_branch
                        )
                    except GitHubHTTPError as error:
                        if error.status_code != 409:
                            raise
                        # GitHub's reference endpoint returns 409 for an empty repo.
                    else:
                        self.raw.save_commit_document(
                            name, run_id, "reference", reference_payload, extracted_at
                        )
                        reference = Reference.model_validate(reference_payload)
                        if (
                            reference.object.type != "commit"
                            or reference.ref
                            != f"refs/heads/{repository.default_branch}"
                        ):
                            raise GitHubPaginationError("Unexpected branch reference.")
                        head = reference.object.sha
                    base = self.postgres.checkpoint(
                        repository.id, repository.default_branch
                    )
                    if full_refresh:
                        base = None
                    pages = []
                    seen: set[str] = set()
                    expected_total = None
                    mode = "empty" if head is None else "unchanged"
                    if head is not None:
                        for page_mode, page in self.extractor.pages(
                            repository.full_name, head, base
                        ):
                            received = page
                            if page_mode == "compare":
                                received = (
                                    page.get("commits")
                                    if isinstance(page, dict)
                                    and page.get("status") == "ahead"
                                    else None
                                )
                            # Count received records even if preserving this page
                            # fails. Domain validation still follows raw storage.
                            if isinstance(received, list):
                                extracted += len(received)
                            path = self.raw.save_commit_document(
                                name,
                                run_id,
                                f"page-{len(pages) + 1:06d}",
                                page,
                                extracted_at,
                            )
                            pages.append((page_mode, path))
                            items = page_commits(page_mode, page)
                            for item in items:
                                seen.add(Commit.model_validate(item).sha)
                            if page_mode == "compare" and page["status"] == "ahead":
                                total = page.get("total_commits")
                                if type(total) is not int or total < 1:
                                    raise GitHubPaginationError(
                                        "Invalid compare count."
                                    )
                                if (
                                    expected_total is not None
                                    and total != expected_total
                                ):
                                    raise GitHubPaginationError("Comparison changed.")
                                expected_total = total
                                mode = "incremental"
                            elif page_mode == "list":
                                mode = "full"
                            logger.info(
                                "commits.page_saved",
                                extra={
                                    **context,
                                    "page": len(pages),
                                    "records_extracted": extracted,
                                },
                            )
                        if mode == "incremental" and len(seen) != expected_total:
                            raise GitHubPaginationError("Incomplete comparison.")
                        if head != base and head not in seen:
                            raise GitHubPaginationError("Missing pinned head commit.")
                    manifest = self.raw.save_commit_document(
                        name,
                        run_id,
                        "manifest",
                        {
                            "repository_id": repository.id,
                            "branch": repository.default_branch,
                            "base_sha": base,
                            "head_sha": head,
                            "mode": mode,
                            "records_extracted": extracted,
                            "pages": [{"mode": m, "path": str(p)} for m, p in pages],
                        },
                        extracted_at,
                    )
                    loaded = self.postgres.complete(
                        repository,
                        run_id,
                        repository_path,
                        manifest,
                        pages,
                        head,
                        extracted,
                        extracted_at,
                    )
            except Exception as error:
                try:
                    self.postgres.fail_run(
                        run_id, extracted, raw_path, type(error).__name__
                    )
                except Exception:
                    summarize(
                        self.summaries,
                        logger,
                        context,
                        extracted,
                        None,
                        "UNKNOWN",
                        started,
                        type(error).__name__,
                    )
                    logger.error("commits.audit_failed", extra=context)
                    raise
                logger.error(
                    "commits.failed",
                    extra={
                        **context,
                        "error_type": type(error).__name__,
                        "records_extracted": extracted,
                        "records_loaded": 0,
                        "duration": time.monotonic() - started,
                    },
                )
                summarize(
                    self.summaries,
                    logger,
                    context,
                    extracted,
                    0,
                    "FAILED",
                    started,
                    type(error).__name__,
                )
                failures += 1
                continue
            logger.info(
                "commits.completed",
                extra={
                    **context,
                    "mode": mode,
                    "head_sha": head,
                    "records_extracted": extracted,
                    "records_loaded": loaded,
                    "duration": time.monotonic() - started,
                },
            )
            summarize(
                self.summaries, logger, context, extracted, loaded, "SUCCESS", started
            )
        return failures
