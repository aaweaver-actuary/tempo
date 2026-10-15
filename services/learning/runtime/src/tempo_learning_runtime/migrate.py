"""Explicit owner-only ordered migrations, never implicit HTTP startup work."""

from pathlib import Path
import hashlib, json
import psycopg
from psycopg import sql


def migrate(owner_url, workspace_id, timezone_name):
    resources = Path(__file__).parent / "migrations"
    manifest = json.loads(resources.joinpath("manifest.json").read_text())
    seen = set()
    with psycopg.connect(owner_url) as database:
        database.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('learning:migrations',0))"
        )
        exists = database.execute(
            "SELECT to_regclass('learning.migration_history')"
        ).fetchone()[0]
        applied = (
            dict(
                database.execute(
                    "SELECT id,checksum FROM learning.migration_history"
                ).fetchall()
            )
            if exists
            else {}
        )
        if set(applied) - {entry["id"] for entry in manifest}:
            raise ValueError("Database has unknown Learning migrations")
        for entry in manifest:
            if entry["id"] in seen or not set(entry["depends_on"]) <= seen:
                raise ValueError("Migration order or identity is invalid")
            path = entry["path"]
            if "/" in path or ".." in path or not path.endswith(".sql"):
                raise ValueError("Migration path escapes package")
            content = resources.joinpath(path).read_text()
            digest = hashlib.sha256(content.encode()).hexdigest()
            if entry["id"] in applied:
                if applied[entry["id"]] != digest:
                    raise ValueError("Applied migration checksum changed")
            else:
                database.execute(content, prepare=False)
                database.execute(
                    "INSERT INTO learning.migration_history(id,checksum) VALUES(%s,%s)",
                    (entry["id"], digest),
                )
            seen.add(entry["id"])
        database.execute(
            "INSERT INTO learning.instance VALUES(1,%s) ON CONFLICT DO NOTHING",
            (workspace_id,),
        )
        if (
            database.execute(
                "SELECT workspace_id FROM learning.instance WHERE id=1"
            ).fetchone()[0]
            != workspace_id
        ):
            raise ValueError("Cannot rebind a workspace database")
        database.execute(
            "INSERT INTO learning.settings(id,timezone) VALUES(1,%s) ON CONFLICT DO NOTHING",
            (timezone_name,),
        )


def provision(
    admin_url,
    database_name,
    owner_role,
    writer_role,
    reader_role,
    writer_password,
    reader_password,
):
    """Provision only an explicitly selected disposable database, not a default live target."""
    if not database_name.startswith("learning_"):
        raise ValueError(
            "Provisioning is restricted to explicitly disposable learning_* databases"
        )
    with psycopg.connect(admin_url, autocommit=True) as database:
        for role, password in [
            (writer_role, writer_password),
            (reader_role, reader_password),
        ]:
            database.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
        database.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(
                sql.Identifier(reader_role)
            )
        )
        database.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(owner_role))
        )
        database.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database_name), sql.Identifier(owner_role)
            )
        )
        database.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database_name)
            )
        )
        for role in [writer_role, reader_role]:
            database.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(role)
                )
            )


def grant_runtime(owner_url, writer_role, reader_role):
    with psycopg.connect(owner_url) as database:
        database.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        database.execute("REVOKE ALL ON SCHEMA learning FROM PUBLIC")
        database.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA learning FROM PUBLIC")
        for role in [writer_role, reader_role]:
            database.execute(
                sql.SQL("GRANT USAGE ON SCHEMA learning TO {}").format(
                    sql.Identifier(role)
                )
            )
            database.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA learning TO {}").format(
                    sql.Identifier(role)
                )
            )
            database.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION learning.json_extract_path_text(text,text[]) TO {}"
                ).format(sql.Identifier(role))
            )
        database.execute(
            sql.SQL(
                "GRANT INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA learning TO {}"
            ).format(sql.Identifier(writer_role))
        )
        database.execute(
            sql.SQL(
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA learning TO {}"
            ).format(sql.Identifier(writer_role))
        )
        database.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION learning.opening_evidence_bind_queue(bigint,text,text) TO {}"
            ).format(sql.Identifier(writer_role))
        )
        database.execute(
            sql.SQL(
                "REVOKE INSERT,UPDATE,DELETE ON learning.migration_history,learning.instance FROM {}"
            ).format(sql.Identifier(writer_role))
        )


def expected_migrations():
    directory = Path(__file__).parent / "migrations"
    return {
        entry["id"]: hashlib.sha256(
            (directory / entry["path"]).read_bytes()
        ).hexdigest()
        for entry in json.loads((directory / "manifest.json").read_text())
    }
