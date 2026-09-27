"""Provision separate read-only API and write-worker roles after schema migration.

Password files must live outside the repository and be supplied to containers as
secrets. The administrative DSN is used only by this maintenance command.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql


def ensure_role(database: psycopg.Connection, name: str, password: str) -> None:
    exists = database.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
    if not exists:
        database.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
    else:
        database.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )


def provision(admin_dsn: str, reader_password: str, writer_password: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as database:
        owner = database.execute("SELECT current_user").fetchone()[0]
        database_name = database.execute("SELECT current_database()").fetchone()[0]
        ensure_role(database, "tempo_reader", reader_password)
        ensure_role(database, "tempo_writer", writer_password)
        for role in ("tempo_reader", "tempo_writer"):
            database.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(role)
                )
            )
            database.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
            )
        database.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO tempo_reader")
        database.execute(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO tempo_writer"
        )
        database.execute(
            "GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO tempo_writer"
        )
        database.execute("ALTER ROLE tempo_reader SET default_transaction_read_only = on")
        database.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO tempo_reader").format(sql.Identifier(owner))
        )
        database.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO tempo_writer")
            .format(sql.Identifier(owner))
        )
        database.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT USAGE,SELECT,UPDATE ON SEQUENCES TO tempo_writer")
            .format(sql.Identifier(owner))
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-dsn", default=os.environ.get("TEMPO_POSTGRES_ADMIN_URL"))
    parser.add_argument("--reader-password-file", type=Path, required=True)
    parser.add_argument("--writer-password-file", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.admin_dsn:
        parser.error("Provide --admin-dsn or TEMPO_POSTGRES_ADMIN_URL")
    provision(
        arguments.admin_dsn,
        arguments.reader_password_file.read_text().strip(),
        arguments.writer_password_file.read_text().strip(),
    )


if __name__ == "__main__":
    main()
