"""JSON logs expose only allowlisted operational fields, never exception bodies."""

import json
import logging
from datetime import UTC, datetime


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
            "run_id",
            "records",
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
        return json.dumps(data, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("ingestion")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
