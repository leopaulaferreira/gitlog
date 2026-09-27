"""Smoke tests for the installed package and its public entry points."""

import subprocess
import sys
import sysconfig
from importlib.metadata import version
from pathlib import Path

import pytest


@pytest.mark.parametrize("args", [[], ["--help"]])
def test_module_displays_bootstrap_help(args: list[str], tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ingestion.main", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "repository ingestion" in result.stdout
    assert "repositories" in result.stdout
    assert "migrate" in result.stdout
    assert "issues" in result.stdout
    assert "pull-requests" in result.stdout
    assert "all" in result.stdout
    assert "--version" in result.stdout
    assert result.stderr == ""


def test_installed_console_script_reports_version(tmp_path: Path) -> None:
    executable = Path(sysconfig.get_path("scripts")) / "gitlog"
    result = subprocess.run(
        [str(executable), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == version("gitlog")


def test_unimplemented_command_fails_clearly(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ingestion.main", "contributors"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice: 'contributors'" in result.stderr


def test_sql_migration_is_available_outside_checkout(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.resources import files; "
            "assert 'CREATE TABLE raw.repositories' in "
            "files('ingestion.db').joinpath('migrations/001_repositories.sql').read_text()",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_cli_invalid_configuration_exits_with_safe_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITLOG_DB_PASSWORD", "")
    result = subprocess.run(
        [sys.executable, "-m", "ingestion.main", "repositories"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert '"event": "command.failed"' in result.stderr
    assert '"error_type": "ValidationError"' in result.stderr
    assert "Traceback" not in result.stderr
