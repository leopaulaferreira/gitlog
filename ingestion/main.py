"""Run explicit schema migrations or the repository ingestion pipeline."""

import argparse
import logging
from importlib.metadata import version

import psycopg
from dotenv import load_dotenv

from ingestion.client import GitHubClient
from ingestion.config import DatabaseSettings, IngestionSettings
from ingestion.db.migrate import migrate
from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.loaders.raw_loader import RawLoader
from ingestion.logging_config import configure_logging
from ingestion.services.ingestion_service import IngestionService


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="gitlog",
        description=(
            "GitLog — Turning GitHub activity into structured data and "
            "actionable insights. Phase 2: repository ingestion."
        ),
    )
    parser.add_argument("--version", action="version", version=version("gitlog"))
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "migrate", help="Apply migrations and provision the ingestion role"
    )
    commands.add_parser("repositories", help="Ingest configured repositories")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    load_dotenv(dotenv_path=".env", override=False)
    configure_logging()
    logger = logging.getLogger("ingestion.main")
    try:
        database = DatabaseSettings.from_env()
        if args.command == "migrate":
            with psycopg.connect(
                **database.connection_kwargs(admin=True), autocommit=True
            ) as connection:
                migrate(connection, database)
            logger.info("migrations.completed")
            return 0
        settings = IngestionSettings.from_env()
        with GitHubClient() as github:
            with psycopg.connect(
                **database.connection_kwargs(), autocommit=True
            ) as connection:
                service = IngestionService(
                    RepositoryExtractor(github),
                    RawLoader(settings.raw_dir),
                    PostgresLoader(connection),
                )
                failures = service.run(settings.repositories)
        return 1 if failures else 0
    except Exception as error:
        logger.error("command.failed", extra={"error_type": type(error).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
