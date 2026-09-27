"""Durable, immutable JSON snapshots, partitioned by UTC extraction date."""

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ingestion.client.github_client import Payload
from ingestion.config import validate_repository_name


class RawLoader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(
        self, repository: str, run_id: UUID, payload: Payload, extracted_at: datetime
    ) -> Path:
        validate_repository_name(repository)
        if extracted_at.tzinfo is None:
            raise ValueError("Extraction time must be timezone aware.")
        instant = extracted_at.astimezone(UTC)
        directory = (
            self.root
            / "repositories"
            / f"repository={repository.lower().replace('/', '_')}"
            / f"year={instant:%Y}"
            / f"month={instant:%m}"
            / f"day={instant:%d}"
        )
        destination = directory / f"{run_id}.json"
        return self._publish(destination, payload)

    def save_commit_document(
        self,
        repository: str,
        run_id: UUID,
        document: str,
        payload: Payload,
        extracted_at: datetime,
    ) -> Path:
        validate_repository_name(repository)
        if extracted_at.tzinfo is None:
            raise ValueError("Extraction time must be timezone aware.")
        if not re.fullmatch(r"repository|reference|manifest|page-[0-9]+", document):
            raise ValueError("Invalid commit snapshot document.")
        instant = extracted_at.astimezone(UTC)
        destination = (
            self.root.resolve()
            / "commits"
            / f"repository={repository.lower().replace('/', '_')}"
            / f"year={instant:%Y}"
            / f"month={instant:%m}"
            / f"day={instant:%d}"
            / str(run_id)
            / f"{document}.json"
        )
        return self._publish(destination, payload)

    @staticmethod
    def _publish(destination: Path, payload: Payload) -> Path:
        directory = destination.parent
        directory.mkdir(parents=True, exist_ok=True)
        # Link an fsynced temporary file atomically without replacing old snapshots.
        fd, temporary = tempfile.mkstemp(
            dir=directory, prefix=".snapshot-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
