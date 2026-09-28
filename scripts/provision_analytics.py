"""Provision dbt writer and BI reader roles, also on existing volumes."""

import logging
import os
import re

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from ingestion.config import DatabaseSettings
from ingestion.logging_config import configure_logging


def provision(connection, settings, dbt_user, dbt_password, reader, reader_password):
    for name, password in ((dbt_user, dbt_password), (reader, reader_password)):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) or name.startswith("pg_"):
            raise ValueError("Invalid analytics role name.")
        if not password:
            raise ValueError("Configure distinct analytics passwords.")
    if len({dbt_user, reader, settings.user, settings.admin_user}) != 4:
        raise ValueError("Analytics, reader, ingestion and admin roles must differ.")
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(7102403)")
        for name, password in ((dbt_user, dbt_password), (reader, reader_password)):
            role = connection.execute(
                "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication "
                "OR rolbypassrls FROM pg_roles WHERE rolname=%s",
                (name,),
            ).fetchone()
            memberships = connection.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                "WHERE r.rolname=%s",
                (name,),
            ).fetchone()
            if (role and role[0]) or memberships:
                raise ValueError("Refusing a privileged or inherited analytics role.")
            action = "ALTER ROLE" if role else "CREATE ROLE"
            connection.execute(
                sql.SQL("{} {} LOGIN NOINHERIT PASSWORD {}").format(
                    sql.SQL(action), sql.Identifier(name), sql.Literal(password)
                )
            )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA raw TO {}").format(sql.Identifier(dbt_user))
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT ON raw.repositories, raw.commits, raw.issues, "
                "raw.pull_requests TO {}"
            ).format(sql.Identifier(dbt_user))
        )
        for schema in ("staging", "intermediate", "analytics"):
            owner = connection.execute(
                "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s",
                (schema,),
            ).fetchone()
            if owner and owner[0] != dbt_user:
                raise ValueError("Refusing a schema owned by another role.")
            connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    sql.Identifier(schema), sql.Identifier(dbt_user)
                )
            )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA analytics TO {}").format(
                sql.Identifier(reader)
            )
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {}").format(
                sql.Identifier(reader)
            )
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA analytics "
                "GRANT SELECT ON TABLES TO {}"
            ).format(sql.Identifier(dbt_user), sql.Identifier(reader))
        )


def main():
    load_dotenv(override=False)
    configure_logging()
    try:
        settings = DatabaseSettings.from_env()
        with psycopg.connect(
            **settings.connection_kwargs(admin=True), autocommit=True
        ) as connection:
            provision(
                connection,
                settings,
                os.environ.get("DBT_USER", "gitlog_dbt"),
                os.environ.get("DBT_PASSWORD", ""),
                os.environ.get("METABASE_READER_USER", "gitlog_metabase"),
                os.environ.get("METABASE_READER_PASSWORD", ""),
            )
        logging.getLogger("ingestion.analytics").info("analytics.provisioned")
        return 0
    except Exception as error:
        logging.getLogger("ingestion.analytics").error(
            "analytics.provision_failed", extra={"error_type": type(error).__name__}
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
