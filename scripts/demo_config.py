"""Fill empty/example local passwords without changing configured credentials."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key


def main():
    path = Path(".env")
    if not path.exists():
        raise SystemExit("Copy .env.example to .env first.")
    values = dotenv_values(path)
    for key in (
        "POSTGRES_PASSWORD",
        "GITLOG_DB_PASSWORD",
        "DBT_PASSWORD",
        "METABASE_READER_PASSWORD",
        "METABASE_DB_PASSWORD",
        "METABASE_ADMIN_PASSWORD",
    ):
        value = values.get(key)
        if not value or value.startswith("replace-with-"):
            set_key(path, key, secrets.token_urlsafe(32) + "aA1!")
    if not values.get("METABASE_ADMIN_EMAIL"):
        set_key(path, "METABASE_ADMIN_EMAIL", "admin@gitlog.local")
    for key, value in [("LOCAL_UID", os.getuid()), ("LOCAL_GID", os.getgid())]:
        set_key(path, key, str(value))
    path.chmod(0o600)
    print("Local .env configured. Existing credentials and GITHUB_TOKEN preserved.")


if __name__ == "__main__":
    main()
