"""Run integration tests in an ephemeral Compose project, never the local database."""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    project = f"gitlog-test-{uuid4().hex[:10]}"
    env = dict(
        os.environ,
        POSTGRES_PORT="0",
        POSTGRES_DB="gitlog_test",
        POSTGRES_USER="gitlog_admin",
        POSTGRES_PASSWORD=secrets.token_urlsafe(24),
    )
    command = [
        "docker",
        "compose",
        "--env-file",
        str(root / ".env.example"),
        "-f",
        str(root / "docker-compose.yml"),
        "-p",
        project,
    ]
    try:
        subprocess.run(
            [*command, "up", "-d", "--wait", "--wait-timeout", "90"],
            env=env,
            check=True,
        )
        address = subprocess.check_output(
            [*command, "port", "postgres", "5432"], env=env, text=True
        ).strip()
        env["GITLOG_TEST_CONFIG"] = json.dumps(
            {
                "host": "127.0.0.1",
                "port": int(address.rsplit(":", 1)[1]),
                "database": "gitlog_test",
                "admin_user": "gitlog_admin",
                "admin_password": env["POSTGRES_PASSWORD"],
                "user": "gitlog_test_writer",
                "password": secrets.token_urlsafe(24),
            }
        )
        runner = (
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--append",
                "--branch",
                "--source=ingestion",
                "-m",
                "pytest",
            ]
            if "--coverage" in sys.argv[1:]
            else [sys.executable, "-m", "pytest"]
        )
        return subprocess.run(
            [*runner, "-m", "integration", "-q"],
            env=env,
            cwd=root,
            check=False,
        ).returncode
    finally:
        subprocess.run([*command, "down", "--volumes"], env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
