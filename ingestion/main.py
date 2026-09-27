"""Project CLI; network access is explicit through GitHubClient in Phase 1."""

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gitlog",
        description=(
            "GitLog — Turning GitHub activity into structured data and "
            "actionable insights. Phase 1: ingestion is not implemented yet."
        ),
    )
    parser.add_argument("--version", action="version", version=version("gitlog"))
    parser.parse_args()
    parser.print_help()


if __name__ == "__main__":
    main()
