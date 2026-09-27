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
    assert "ingestion is not implemented yet" in result.stdout
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
        [sys.executable, "-m", "ingestion.main", "commits"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "unrecognized arguments: commits" in result.stderr
