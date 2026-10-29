"""Installed, read-only database proof before Learning application startup.

Physical cluster identity is supplied by the caller's separate read-only volume
probe. Restricted runtime credentials are deliberately not granted access to
pg_control_system(), and this module never labels that value SQL-observed.
"""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from zoneinfo import ZoneInfo


class RuntimeReadbackError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise RuntimeReadbackError(code)


def _load_dependencies():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from tempo_learning_database.ddl.migrate import expected_migrations, schema_identity
    return psycopg.connect, conninfo_to_dict, expected_migrations, schema_identity


def _passfile(path):
    _require(isinstance(path, str) and Path(path).is_absolute(), "private_passfile_required")
    try:
        info = Path(path).lstat()
    except OSError:
        raise RuntimeReadbackError("private_passfile_unavailable") from None
    _require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077
             and info.st_uid == os.getuid(), "private_passfile_required")
    return path


def _connection_info(dsn, parse, *, database, role, port):
    _require(isinstance(dsn, str) and bool(dsn), "runtime_connection_required")
    try:
        values = parse(dsn)
    except Exception:
        raise RuntimeReadbackError("runtime_connection_invalid") from None
    # Persistent bootstrap emits exactly these credential-free identity fields.
    # Host routing and the password file are supplied explicitly below, never by
    # a hidden service/options/password/hostaddr setting embedded in the DSN.
    _require(set(values) == {"host", "port", "dbname", "user"}, "runtime_connection_invalid")
    _require(values["dbname"] == database and values["user"] == role
             and str(values["port"]) == str(port), "runtime_connection_identity_mismatch")
    host = values["host"]
    _require(isinstance(host, str) and bool(host.strip()) and "," not in host
             and not host.startswith(("/", "@")), "runtime_connection_invalid")
    return dsn


_SESSION = (
    "SELECT current_database(),session_user,current_user,"
    "host(inet_server_addr()),inet_server_port(),"
    "current_setting('transaction_read_only'),current_setting('default_transaction_read_only')"
)
_ROLE = (
    "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit,"
    "has_schema_privilege(current_user,'learning','CREATE'),rolconfig "
    "FROM pg_roles WHERE rolname=current_user"
)
_MEMBERSHIPS = (
    "SELECT rolname FROM pg_roles WHERE rolname<>current_user "
    "AND pg_has_role(current_user,oid,'MEMBER')"
)
_READER_WRITES = (
    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname='learning' AND c.relkind IN ('r','p','v','m','f') AND "
    "(has_table_privilege(current_user,c.oid,'INSERT') OR "
    "has_table_privilege(current_user,c.oid,'UPDATE') OR "
    "has_table_privilege(current_user,c.oid,'DELETE') OR "
    "has_table_privilege(current_user,c.oid,'TRUNCATE'))"
)


def _inspect(connection, *, database, role, hostaddr, port, reader,
             workspace, timezone, migrations):
    connection.read_only = True
    session = connection.execute(_SESSION).fetchone()
    _require(session is not None and session[:3] == (database, role, role)
             and session[3] == hostaddr and session[4] == port
             and session[5] == "on", "runtime_session_identity_mismatch")
    if reader:
        _require(session[6] == "on", "reader_default_not_read_only")
    flags = connection.execute(_ROLE).fetchone()
    _require(flags is not None and flags[:8] == (True, False, False, False, False, False, False, False),
             "runtime_role_not_restricted")
    defaults = flags[8] or []
    _require(("default_transaction_read_only=on" in defaults) is reader,
             "runtime_role_read_only_default_mismatch")
    _require(not connection.execute(_MEMBERSHIPS).fetchall(), "runtime_role_membership_unexpected")
    if reader:
        _require(not connection.execute(_READER_WRITES).fetchall(), "reader_write_privilege_unexpected")
    rows = connection.execute("SELECT id,checksum FROM learning.migration_history").fetchall()
    _require(len(rows) == len(migrations) and dict(rows) == migrations, "runtime_schema_mismatch")
    metadata = connection.execute(
        "SELECT instance.workspace_id,settings.timezone FROM learning.instance "
        "JOIN learning.settings ON settings.id=instance.id WHERE instance.id=1"
    ).fetchall()
    _require(metadata == [(workspace, timezone)], "runtime_workspace_mismatch")


def verify_runtime(*, expected_database, expected_workspace, expected_schema,
                   expected_system_identifier, expected_hostaddr, expected_port,
                   expected_timezone, environ=None, _dependencies=None):
    """Observe two restricted connections; never connect with an owner/admin role.

    No empty-content/fresh-activation requirement applies to an installed target.
    ``system_identifier`` is a caller-bound physical probe identity, not a claim
    that restricted SQL queried PostgreSQL control data. The caller must retain
    and independently validate its before/after physical container/volume proof.
    """
    env = os.environ if environ is None else environ
    _require(not any(key.startswith("PG") and key != "PGPASSFILE" and value
                     for key, value in env.items()), "libpq_override_forbidden")
    _require(isinstance(expected_database, str)
             and re.fullmatch(r"[a-z][a-z0-9_]{0,47}", expected_database)
             and expected_database not in {"postgres", "template0", "template1"}, "expected_database_invalid")
    _require(isinstance(expected_workspace, str) and bool(expected_workspace.strip()), "expected_workspace_invalid")
    _require(isinstance(expected_schema, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", expected_schema),
             "expected_schema_invalid")
    _require(isinstance(expected_system_identifier, str)
             and re.fullmatch(r"[1-9][0-9]{0,19}", expected_system_identifier)
             and int(expected_system_identifier) < 2 ** 64, "expected_system_identifier_invalid")
    try:
        hostaddr = str(ipaddress.ip_address(expected_hostaddr))
        ZoneInfo(expected_timezone)
    except (ValueError, TypeError, KeyError):
        raise RuntimeReadbackError("expected_endpoint_or_timezone_invalid") from None
    _require(type(expected_port) is int and 1 <= expected_port <= 65535, "expected_port_invalid")
    _require(env.get("TEMPO_LEARNING_WORKSPACE") == expected_workspace
             and env.get("TEMPO_LEARNING_TIMEZONE") == expected_timezone, "runtime_configuration_mismatch")
    passfile = _passfile(env.get("PGPASSFILE"))
    connect, parse, expected_migrations, schema_identity = _dependencies or _load_dependencies()
    migrations = expected_migrations()
    _require(schema_identity(migrations) == expected_schema, "selected_source_schema_mismatch")
    roles = {kind: expected_database + "_" + kind for kind in ("reader", "writer")}
    dsns = {
        "reader": env.get("TEMPO_LEARNING_READER_URL"),
        "writer": env.get("TEMPO_LEARNING_DATABASE_URL"),
    }
    for kind in ("reader", "writer"):
        _connection_info(dsns[kind], parse, database=expected_database, role=roles[kind], port=expected_port)
    for kind in ("reader", "writer"):
        try:
            with connect(dsns[kind], hostaddr=hostaddr, port=expected_port, passfile=passfile,
                         connect_timeout=2, autocommit=False,
                         options="-c default_transaction_read_only=on -c statement_timeout=1500 -c lock_timeout=250 -c transaction_timeout=2500") as connection:
                _inspect(connection, database=expected_database, role=roles[kind],
                         hostaddr=hostaddr, port=expected_port, reader=kind == "reader",
                         workspace=expected_workspace, timezone=expected_timezone, migrations=migrations)
        except RuntimeReadbackError:
            raise
        except Exception:
            # Driver errors can include passwords or connection strings. The
            # public result exposes a fixed code only; never echo their text.
            raise RuntimeReadbackError("runtime_connection_unavailable") from None
    result = {
        "contract_version": 1, "status": "verified", "scope": "observed", "read_only": True,
        "database": expected_database, "workspace_id": expected_workspace,
        "timezone": expected_timezone, "schema": expected_schema, "roles": roles,
        "server": {"hostaddr": hostaddr, "port": expected_port,
                   "system_identifier": expected_system_identifier, "identity_scope": "caller-bound"},
    }
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {**result, "evidenceId": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "workspace", "schema", "system-identifier", "hostaddr", "timezone"):
        parser.add_argument("--expected-" + name, required=True)
    parser.add_argument("--expected-port", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = verify_runtime(**vars(args))
    except RuntimeReadbackError as error:
        print(json.dumps({"contract_version": 1, "status": "blocked", "code": error.code}))
        return 1
    except Exception:
        print(json.dumps({"contract_version": 1, "status": "blocked", "code": "runtime_readback_unavailable"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
