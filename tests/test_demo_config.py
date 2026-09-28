"""Local setup must preserve existing credentials and create real random secrets."""

from dotenv import dotenv_values

from scripts.demo_config import main
from scripts.setup_metabase import find_named, require_managed


def test_demo_config_preserves_configured_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".env"
    path.write_text(
        "GITHUB_TOKEN=existing-token\nPOSTGRES_PASSWORD=existing-db-password\n"
        "DBT_PASSWORD=replace-with-a-dbt-password\n"
    )
    main()
    before = dotenv_values(path)
    main()
    assert dotenv_values(path) == before
    assert before["GITHUB_TOKEN"] == "existing-token"
    assert before["POSTGRES_PASSWORD"] == "existing-db-password"
    passwords = [
        before[key]
        for key in (
            "DBT_PASSWORD",
            "GITLOG_DB_PASSWORD",
            "METABASE_READER_PASSWORD",
            "METABASE_DB_PASSWORD",
            "METABASE_ADMIN_PASSWORD",
        )
    ]
    assert len(set(passwords)) == 5
    assert all(len(value) >= 32 for value in passwords)
    assert path.stat().st_mode & 0o777 == 0o600
    assert "existing-token" not in capsys.readouterr().out


def test_setup_refuses_ambiguous_or_unmanaged_objects():
    import pytest

    with pytest.raises(ValueError, match="Ambiguous"):
        find_named(
            [{"name": "GitLog Analytics"}, {"name": "GitLog Analytics"}],
            "GitLog Analytics",
        )
    with pytest.raises(ValueError, match="not managed"):
        require_managed({"description": "Personal dashboard"})


def test_localization_finds_existing_object_by_legacy_name():
    original = {"id": 42, "name": "Commits Collected"}
    assert find_named([original], "Commits coletados", "Commits Collected") is original
    renamed = {"id": 42, "name": "Commits coletados"}
    assert find_named([renamed], "Commits coletados", "Commits Collected") is renamed


def test_localization_refuses_competing_legacy_and_translated_objects():
    import pytest

    with pytest.raises(ValueError, match="Ambiguous"):
        find_named(
            [
                {"id": 42, "name": "Commits Collected"},
                {"id": 43, "name": "Commits coletados"},
            ],
            "Commits coletados",
            "Commits Collected",
        )
