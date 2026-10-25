"""Explicit owner-only ordered migrations, never implicit HTTP startup work."""

from pathlib import Path
import hashlib, json, re
import psycopg
from psycopg import sql
import tempo_remediation_database


def migration_source(entry):
    path = entry["path"]
    if "/" in path or ".." in path or not path.endswith(".sql"):
        raise ValueError("Migration path escapes package")
    package = entry.get("package", "tempo_learning_runtime")
    if package not in {"tempo_learning_runtime", "tempo_learning_remediation"}:
        raise ValueError("Undeclared Learning migration package")
    directory = (
        Path(tempo_remediation_database.__file__).parent / "ddl"
        if package == "tempo_learning_remediation"
        else Path(__file__).parent
    )
    return directory.joinpath("migrations", path).read_bytes()


def migrate(owner_url, workspace_id, timezone_name, *, expected_identity=None, owner_role=None):
    resources = Path(__file__).parent / "migrations"
    manifest = json.loads(resources.joinpath("manifest.json").read_text())
    seen = set()
    with psycopg.connect(owner_url) as database:
        _bind_persistent_connection(database, expected_identity, owner_role)
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
            content = migration_source(entry)
            digest = hashlib.sha256(content).hexdigest()
            if entry["id"] in applied:
                if applied[entry["id"]] != digest:
                    raise ValueError("Applied migration checksum changed")
            else:
                database.execute("SET LOCAL search_path=learning,pg_catalog")
                database.execute(content.decode(), prepare=False)
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
        _create_database_and_roles(database, database_name, owner_role, writer_role,
                                   reader_role, writer_password, reader_password)


def _create_database_and_roles(database, database_name, owner_role, writer_role,
                               reader_role, writer_password, reader_password, *, empty_template=False):
    create_database_sql = "CREATE DATABASE {} OWNER {}" + (" TEMPLATE template0" if empty_template else "")
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
        sql.SQL(create_database_sql).format(
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


def grant_runtime(owner_url, writer_role, reader_role, *, expected_identity=None, owner_role=None):
    with psycopg.connect(owner_url) as database:
        _bind_persistent_connection(database, expected_identity, owner_role)
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
        entry["id"]: hashlib.sha256(migration_source(entry)).hexdigest()
        for entry in json.loads((directory / "manifest.json").read_text())
    }


# Persistent creation is distinct from disposable provision(); its guard is unchanged.
_PERSISTENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_SERVER_IDENTITY = (
    "SELECT system_identifier::text,current_database(),session_user,current_user "
    "FROM pg_control_system()"
)


def _persistent_names(database_name, owner_role, writer_role, reader_role):
    if (not isinstance(database_name, str) or not _PERSISTENT_NAME.fullmatch(database_name)
            or database_name in {"postgres", "template0", "template1"}
            or database_name.startswith("learning_study_test_")
            or (owner_role, writer_role, reader_role) !=
               (database_name + "_owner", database_name + "_writer", database_name + "_reader")):
        raise ValueError("Explicit independent persistent database and exact new roles required")


def _assert_persistent_identity(database, expected):
    actual = database.execute(_SERVER_IDENTITY).fetchone()
    if actual != (expected["system_identifier"], expected["database"],
                  expected["session_user"], expected["session_user"]):
        raise ValueError("Persistent server/session identity mismatch")


def _bind_persistent_connection(database, expected_identity, owner_role):
    if expected_identity is None:
        if owner_role is not None:
            raise ValueError("Pinned persistent identity required before selecting owner role")
        return
    if not isinstance(owner_role, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", owner_role):
        raise ValueError("Explicit persistent owner role required")
    _assert_persistent_identity(database, expected_identity)
    database.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner_role)))


def _persistent_absence(database, database_name, roles):
    if database.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database_name,)).fetchone() is not None:
        raise ValueError("Persistent database already exists; no adoption or reset")
    if database.execute("SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)", (list(roles),)).fetchall():
        raise ValueError("Persistent role already exists; no adoption or reset")


def inspect_persistent_target(admin_url, database_name, owner_role, writer_role,
                              reader_role, *, system_identifier, session_user):
    """Read-only physical identity and absence check before generating credentials."""
    _persistent_names(database_name, owner_role, writer_role, reader_role)
    expected = {"system_identifier": system_identifier, "database": "postgres", "session_user": session_user}
    with psycopg.connect(admin_url, autocommit=True) as database:
        _assert_persistent_identity(database, expected)
        _persistent_absence(database, database_name, (owner_role, writer_role, reader_role))


def provision_persistent(admin_url, database_name, owner_role, writer_role,
                         reader_role, writer_password, reader_password, *,
                         system_identifier, session_user):
    """Create absent exact targets on a pinned server; never adopt, reset or DROP.

    CREATE DATABASE is not atomic with role creation. Failures leave partial new
    resources for explicit inspection. A session advisory lock serializes this
    database's identity/absence recheck and creation on that physical connection.
    """
    _persistent_names(database_name, owner_role, writer_role, reader_role)
    if (not isinstance(writer_password, str) or not isinstance(reader_password, str)
            or min(len(writer_password), len(reader_password)) < 32 or writer_password == reader_password):
        raise ValueError("Distinct private runtime passwords required")
    expected = {"system_identifier": system_identifier, "database": "postgres", "session_user": session_user}
    with psycopg.connect(admin_url, autocommit=True) as database:
        _assert_persistent_identity(database, expected)
        database.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))",
                         ("learning:persistent-provision:" + database_name,))
        _persistent_absence(database, database_name, (owner_role, writer_role, reader_role))
        _create_database_and_roles(database, database_name, owner_role, writer_role,
                                   reader_role, writer_password, reader_password, empty_template=True)


def _persistent_owner(database, owner_role):
    if database.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
    ).fetchone() != (owner_role,):
        raise ValueError("Persistent database ownership changed")


def verify_new_persistent_database(owner_admin_url, *, expected_identity, owner_role):
    """Reject any user relation in the newly created target before schema setup."""
    with psycopg.connect(owner_admin_url) as database:
        _assert_persistent_identity(database, expected_identity)
        _persistent_owner(database, owner_role)
        if database.execute(
                "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind IN ('r','p','v','m','S','f') "
                "AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'"
        ).fetchall():
            raise ValueError("New persistent database is not empty")


def verify_persistent_initialized(owner_admin_url, workspace_id, timezone_name,
                                   owner_role, writer_role, reader_role, *, expected_identity):
    """Read back exact ordered schema, fresh metadata, roles and empty content."""
    expected = expected_migrations()
    with psycopg.connect(owner_admin_url) as database:
        _assert_persistent_identity(database, expected_identity)
        _persistent_owner(database, owner_role)
        applied = dict(database.execute("SELECT id,checksum FROM learning.migration_history").fetchall())
        if not expected or applied != expected:
            raise ValueError("Persistent ordered schema/checksum mismatch")
        if database.execute("SELECT workspace_id FROM learning.instance").fetchall() != [(workspace_id,)]:
            raise ValueError("Persistent workspace mismatch")
        if database.execute("SELECT timezone FROM learning.settings").fetchall() != [(timezone_name,)]:
            raise ValueError("Persistent timezone mismatch")
        if database.execute(
                "SELECT id,run_id,epoch FROM learning.frozen_opening_activation"
        ).fetchall() != [(1, None, 0)]:
            raise ValueError("Persistent activation seed is not fresh")
        metadata = {"migration_history", "instance", "settings", "frozen_opening_activation"}
        for (table,) in database.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='learning' ORDER BY tablename"
        ).fetchall():
            if table not in metadata and database.execute(
                    sql.SQL("SELECT 1 FROM learning.{} LIMIT 1").format(sql.Identifier(table))
            ).fetchone() is not None:
                raise ValueError("Persistent target contains content or activity")
        rows = database.execute(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,"
            "rolbypassrls,rolinherit,rolconfig FROM pg_roles WHERE rolname=ANY(%s)",
            ([owner_role, writer_role, reader_role],)
        ).fetchall()
        roles = {row[0]: row for row in rows}
        if set(roles) != {owner_role, writer_role, reader_role}:
            raise ValueError("Persistent runtime roles missing")
        for name, row in roles.items():
            if row[1] is not (name != owner_role) or any(row[2:7]):
                raise ValueError("Persistent role privileges differ")
            if name != owner_role and row[7] is not False:
                raise ValueError("Persistent login role inherits privileges")
        if "default_transaction_read_only=on" not in (roles[reader_role][8] or []):
            raise ValueError("Persistent reader is not read-only")
    return {"expected_migrations": expected, "applied_migrations": applied,
            "identity": schema_identity(applied), "migration_count": len(expected)}


def schema_identity(checksums):
    """Public ordered-schema identity, distinct from receipt format or count."""
    if (type(checksums) is not dict or not checksums
            or any(type(key) is not str or not key.strip()
                   or type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for key, value in checksums.items())):
        raise ValueError("Nonempty migration ID to SHA256 map required")
    encoded = json.dumps(checksums, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
