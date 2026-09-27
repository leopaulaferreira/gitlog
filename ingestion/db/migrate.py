"""Transactional, forward-only SQL migrations with checksums and a global lock."""

import hashlib
from importlib.resources import files

import psycopg
from psycopg import sql

from ingestion.config import DatabaseSettings


class MigrationError(Exception):
    """Migration history or role configuration is inconsistent."""


def migrate(connection: psycopg.Connection, settings: DatabaseSettings) -> None:
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(7102402)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS public.gitlog_schema_migrations (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )"""
        )
        applied = dict(
            connection.execute(
                "SELECT version, checksum FROM public.gitlog_schema_migrations"
            ).fetchall()
        )
        migrations = sorted(
            (
                item
                for item in files("ingestion.db").joinpath("migrations").iterdir()
                if item.name.endswith(".sql")
            ),
            key=lambda item: item.name,
        )
        if set(applied) - {item.name for item in migrations}:
            raise MigrationError("Unknown migration in the database.")
        for migration in migrations:
            content = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(content.encode()).hexdigest()
            if migration.name in applied:
                if applied[migration.name] != checksum:
                    raise MigrationError("An applied migration was modified.")
                continue
            connection.execute(content)
            connection.execute(
                "INSERT INTO public.gitlog_schema_migrations (version, checksum) "
                "VALUES (%s, %s)",
                (migration.name, checksum),
            )
        _provision_writer(connection, settings)


def _provision_writer(
    connection: psycopg.Connection, settings: DatabaseSettings
) -> None:
    role = connection.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname = %s",
        (settings.user,),
    ).fetchone()
    if role is not None and any(role):
        raise MigrationError("Refusing to use a privileged ingestion role.")
    membership = connection.execute(
        "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
        "WHERE r.rolname = %s",
        (settings.user,),
    ).fetchone()
    if membership:
        raise MigrationError("Ingestion role must not inherit other roles.")
    operation = sql.SQL("CREATE ROLE") if role is None else sql.SQL("ALTER ROLE")
    connection.execute(
        sql.SQL("{} {} LOGIN NOINHERIT PASSWORD {}").format(
            operation,
            sql.Identifier(settings.user),
            sql.Literal(settings.password.get_secret_value()),
        )
    )
    connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA raw TO {}").format(sql.Identifier(settings.user))
    )
    connection.execute(
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE ON raw.repositories, raw.pipeline_runs, "
            "raw.commits, raw.ingestion_checkpoints, raw.issues, raw.pull_requests, "
            "raw.entity_checkpoints TO {}"
        ).format(sql.Identifier(settings.user))
    )
