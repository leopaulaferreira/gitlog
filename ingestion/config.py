"""Explicit environment configuration; loading .env belongs to the CLI."""

import os
import re
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def validate_repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Expected owner/repository.")
    if value.split("/")[1] in (".", ".."):
        raise ValueError("Invalid repository name.")
    return value


class IngestionSettings(BaseModel):
    repositories: tuple[str, ...]
    raw_dir: Path = Path("data/raw")

    @field_validator("repositories")
    @classmethod
    def monitored_repositories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("Configure at least one repository.")
        result: dict[str, str] = {}
        for value in values:
            name = validate_repository_name(value.strip())
            result.setdefault(name.lower(), name)
        return tuple(result.values())

    @classmethod
    def from_env(cls) -> Self:
        configured = os.environ.get("GITLOG_REPOSITORIES", "")
        return cls(
            repositories=tuple(configured.split(",")) if configured else (),
            raw_dir=Path(os.environ.get("GITLOG_RAW_DIR", "data/raw")),
        )


class DatabaseSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = "gitlog"
    admin_user: str = "gitlog"
    admin_password: SecretStr = SecretStr("")
    user: str = "gitlog_ingestion"
    password: SecretStr

    @field_validator("database", "admin_user", "user")
    @classmethod
    def identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
            raise ValueError("Use a lowercase PostgreSQL identifier.")
        return value

    @field_validator("password")
    @classmethod
    def nonempty_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("Configure GITLOG_DB_PASSWORD.")
        return value

    @model_validator(mode="after")
    def separate_roles(self) -> Self:
        if self.user == self.admin_user or self.user.startswith("pg_"):
            raise ValueError(
                "The ingestion role must be separate from the administrator."
            )
        return self

    @classmethod
    def from_env(cls) -> Self:
        return cls.model_validate(
            {
                "host": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                "port": os.environ.get("POSTGRES_PORT", "5432"),
                "database": os.environ.get("POSTGRES_DB", "gitlog"),
                "admin_user": os.environ.get("POSTGRES_USER", "gitlog"),
                "admin_password": os.environ.get("POSTGRES_PASSWORD", ""),
                "user": os.environ.get("GITLOG_DB_USER", "gitlog_ingestion"),
                "password": os.environ.get("GITLOG_DB_PASSWORD", ""),
            }
        )

    def connection_kwargs(self, *, admin: bool = False) -> dict[str, str | int]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.admin_user if admin else self.user,
            "password": (
                self.admin_password if admin else self.password
            ).get_secret_value(),
            "connect_timeout": 10,
            "application_name": "gitlog_migrations" if admin else "gitlog_ingestion",
            "options": (
                "-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000"
            ),
        }
