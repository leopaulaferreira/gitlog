"""Destructive setup is permitted only in the runner's isolated test database."""

import json
import os
from collections.abc import Iterator

import psycopg
import pytest

from ingestion.config import DatabaseSettings
from ingestion.db.migrate import migrate


@pytest.fixture(scope="session")
def database_settings() -> DatabaseSettings:
    configured = os.environ.get("GITLOG_TEST_CONFIG")
    if not configured:
        pytest.skip("Run make test-integration to provision an isolated PostgreSQL.")
    settings = DatabaseSettings.model_validate(json.loads(configured))
    if settings.database != "gitlog_test" or settings.host != "127.0.0.1":
        pytest.fail(
            "Integration tests only support the isolated local gitlog_test database."
        )
    with psycopg.connect(
        **settings.connection_kwargs(admin=True), autocommit=True
    ) as connection:
        migrate(connection, settings)
    return settings


@pytest.fixture
def admin(database_settings: DatabaseSettings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        **database_settings.connection_kwargs(admin=True), autocommit=True
    ) as connection:
        connection.execute(
            "TRUNCATE raw.issues, raw.pull_requests, raw.entity_checkpoints, "
            "raw.commits, raw.ingestion_checkpoints, "
            "raw.repositories, raw.pipeline_runs"
        )
        yield connection


@pytest.fixture
def writer(
    admin: psycopg.Connection, database_settings: DatabaseSettings
) -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        **database_settings.connection_kwargs(), autocommit=True
    ) as connection:
        yield connection
