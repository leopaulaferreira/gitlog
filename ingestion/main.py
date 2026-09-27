"""Run schema migrations and repository, commit, issue and pull request ingestion."""

import argparse
import logging
from importlib.metadata import version

import psycopg
from dotenv import load_dotenv

from ingestion.client import GitHubClient
from ingestion.config import DatabaseSettings, IngestionSettings
from ingestion.db.migrate import migrate
from ingestion.extractors.commits import CommitExtractor
from ingestion.extractors.issues import IssueExtractor
from ingestion.extractors.pull_requests import PullRequestExtractor
from ingestion.extractors.repositories import RepositoryExtractor
from ingestion.loaders.commit_loader import CommitLoader
from ingestion.loaders.postgres_loader import PostgresLoader
from ingestion.loaders.raw_loader import RawLoader
from ingestion.loaders.updated_entity_loader import IssueLoader, PullRequestLoader
from ingestion.logging_config import configure_logging
from ingestion.services.commit_service import CommitService
from ingestion.services.ingestion_service import IngestionService
from ingestion.services.updated_entity_service import UpdatedEntityService


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="gitlog",
        description=(
            "GitLog — Turning GitHub activity into structured data and "
            "actionable insights. Phase 4: repository ingestion, "
            "commits, issues and pull requests."
        ),
    )
    parser.add_argument("--version", action="version", version=version("gitlog"))
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "migrate", help="Apply migrations and provision the ingestion role"
    )
    commands.add_parser("repositories", help="Ingest configured repositories")
    for name, help_text in (
        ("commits", "Ingest commits incrementally"),
        ("issues", "Ingest issues excluding pull requests"),
        ("pull-requests", "Ingest pull requests with merge metadata"),
        ("all", "Run repositories, commits, issues and pull requests"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--full-refresh",
            action="store_true",
            help="Reconcile all available records",
        )
        command.add_argument(
            "--per-page", type=int, choices=range(1, 101), default=100, metavar="1..100"
        )
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
                raw = RawLoader(settings.raw_dir)
                selected = (
                    ("repositories", "commits", "issues", "pull-requests")
                    if args.command == "all"
                    else (args.command,)
                )
                failures = 0
                for command in selected:
                    if command == "repositories":
                        failures += IngestionService(
                            RepositoryExtractor(github), raw, PostgresLoader(connection)
                        ).run(settings.repositories)
                    elif command == "commits":
                        failures += CommitService(
                            CommitExtractor(github, per_page=args.per_page),
                            raw,
                            CommitLoader(connection),
                        ).run(settings.repositories, full_refresh=args.full_refresh)
                    else:
                        extractor, loader = (
                            (IssueExtractor, IssueLoader)
                            if command == "issues"
                            else (PullRequestExtractor, PullRequestLoader)
                        )
                        failures += UpdatedEntityService(
                            extractor(github, per_page=args.per_page),
                            raw,
                            loader(connection),
                        ).run(settings.repositories, full_refresh=args.full_refresh)
        return 1 if failures else 0
    except Exception as error:
        logger.error("command.failed", extra={"error_type": type(error).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
