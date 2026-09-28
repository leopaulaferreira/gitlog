"""The BI account reads analytics but cannot see raw or mutate marts."""

import secrets

import psycopg
import pytest

from scripts.provision_analytics import provision

pytestmark = pytest.mark.integration


def test_analytics_roles_and_future_table_grants(admin, database_settings):
    dbt_user, reader = "gitlog_dbt_test", "gitlog_bi_test"
    dbt_password, reader_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    for _ in range(2):
        provision(
            admin, database_settings, dbt_user, dbt_password, reader, reader_password
        )
    kwargs = database_settings.connection_kwargs()
    with psycopg.connect(
        **{**kwargs, "user": dbt_user, "password": dbt_password}, autocommit=True
    ) as dbt:
        dbt.execute("SELECT * FROM raw.repositories")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            dbt.execute("DELETE FROM raw.repositories")
        dbt.execute("CREATE TABLE analytics.permission_probe (id integer)")
        dbt.execute("INSERT INTO analytics.permission_probe VALUES (1)")
    with psycopg.connect(
        **{**kwargs, "user": reader, "password": reader_password}, autocommit=True
    ) as bi:
        assert bi.execute("SELECT * FROM analytics.permission_probe").fetchall() == [
            (1,)
        ]
        for statement in (
            "SELECT * FROM raw.repositories",
            "INSERT INTO analytics.permission_probe VALUES (2)",
            "CREATE TABLE analytics.forbidden(id int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                bi.execute(statement)
