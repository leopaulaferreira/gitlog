"""One canonical summary per repository/entity; context also follows HTTP retries."""

import logging
import time
from contextvars import ContextVar
from functools import wraps

run_context: ContextVar[dict | None] = ContextVar("gitlog_run_context", default=None)


def scoped_run(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        token = run_context.set({})
        self.summaries = []
        try:
            return method(self, *args, **kwargs)
        finally:
            run_context.reset(token)

    return wrapped


def summarize(
    summaries: list[dict],
    logger: logging.Logger,
    context: dict,
    extracted: int,
    loaded: int | None,
    status: str,
    started: float,
    error_type: str | None = None,
) -> None:
    result = {
        **context,
        "status": status,
        "records_extracted": extracted,
        "records_loaded": loaded,
        "records_skipped": extracted - loaded if status == "SUCCESS" else 0,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
    }
    if error_type:
        result["error_type"] = error_type
    summaries.append(result)
    logger.log(
        logging.INFO if status == "SUCCESS" else logging.ERROR,
        "pipeline.summary",
        extra=result,
    )


def print_summaries(summaries: list[dict]) -> None:
    for row in summaries:
        loaded = row["records_loaded"]
        print(
            f"Repository: {row['repository']} | Entity: {row['entity']} | "
            f"Extracted: {row['records_extracted']} | "
            f"Loaded: {loaded if loaded is not None else '?'} | "
            f"Skipped: {row['records_skipped']} | Status: {row['status']} | "
            f"Duration: {row['duration_ms'] / 1000:.3f}s"
        )
