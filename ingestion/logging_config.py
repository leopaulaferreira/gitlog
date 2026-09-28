"""JSON logs expose only allowlisted operational fields, never exception bodies."""

import json
import logging
from datetime import UTC, datetime

from ingestion.observability import run_context


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "event": record.getMessage(),
        }
        for key in (
            "repository",
            "entity",
            "run_id",
            "records",
            "records_extracted",
            "records_loaded",
            "records_skipped",
            "status",
            "duration_ms",
            "page",
            "mode",
            "head_sha",
            "duration",
            "error_type",
            "status_code",
            "attempt",
            "retry",
            "remaining",
            "delay_seconds",
        ):
            if hasattr(record, key):
                data[key] = getattr(record, key)
            elif key in (run_context.get() or {}):
                data[key] = run_context.get()[key]
        return json.dumps(data, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("ingestion")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
