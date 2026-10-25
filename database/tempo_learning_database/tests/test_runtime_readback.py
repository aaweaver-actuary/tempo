"""Public readback protocol fixtures; no real PostgreSQL connection is made."""

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "runtime_readback.py"
SPEC = importlib.util.spec_from_file_location("runtime_readback_under_test", MODULE_PATH)
readback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readback)
MIGRATIONS = {"fixture-migration": "a" * 64}
SCHEMA = "sha256:" + "b" * 64
DATABASE = "tempo_readback_fixture"


class Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class Connection:
    def __init__(self, kind, events):
        self.kind, self.events = kind, events
        self.read_only = False
        self.role = DATABASE + "_" + kind
        self.database = DATABASE
        self.hostaddr, self.port = "192.0.2.7", 5432
        self.transaction_read_only = "on"
        self.default_read_only = "on"
        self.flags = (True, False, False, False, False, False, False, False)
        self.defaults = ["default_transaction_read_only=on"] if kind == "reader" else []
        self.memberships, self.reader_writes = [], []
        self.migrations = list(MIGRATIONS.items())
        self.metadata = [("fixture-workspace", "America/New_York")]
        self.populated_study_rows = [{"card": "already-learned", "review_count": 20}]

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def execute(self, query):
        self.events.append((self.kind, query, self.read_only))
        if not self.read_only:
            raise AssertionError("Read-only must be selected before any query")
        if query.startswith("SELECT current_database()"):
            return Cursor((self.database, self.role, self.role, self.hostaddr, self.port,
                           self.transaction_read_only, self.default_read_only))
        if query.startswith("SELECT rolcanlogin"):
            return Cursor((*self.flags, self.defaults))
        if query.startswith("SELECT rolname FROM pg_roles"):
            return Cursor(self.memberships)
        if query.startswith("SELECT c.relname FROM pg_class"):
            return Cursor(self.reader_writes)
        if query.startswith("SELECT id,checksum"):
            return Cursor(self.migrations)
        if query.startswith("SELECT instance.workspace_id"):
            return Cursor(self.metadata)
        raise AssertionError("Unexpected operation: no content scans, writes or control-system query allowed")


class RuntimeReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tempo-runtime-readback-fake-")
        self.addCleanup(self.temp.cleanup)
        self.passfile = Path(self.temp.name) / "runtime.pgpass"
        self.passfile.write_text("synthetic-private-file-only")
        self.passfile.chmod(0o600)
        self.env = {
            "TEMPO_LEARNING_DATABASE_URL": f"host=postgres port=5432 dbname={DATABASE} user={DATABASE}_writer",
            "TEMPO_LEARNING_READER_URL": f"host=postgres port=5432 dbname={DATABASE} user={DATABASE}_reader",
            "TEMPO_LEARNING_WORKSPACE": "fixture-workspace",
            "TEMPO_LEARNING_TIMEZONE": "America/New_York",
            "PGPASSFILE": str(self.passfile),
        }
        self.events, self.connects = [], []
        self.connections = {kind: Connection(kind, self.events) for kind in ("reader", "writer")}
        self.options = dict(expected_database=DATABASE, expected_workspace="fixture-workspace",
                            expected_schema=SCHEMA, expected_system_identifier="123456789",
                            expected_hostaddr="192.0.2.7", expected_port=5432,
                            expected_timezone="America/New_York")

    def connect(self, dsn, **kwargs):
        self.connects.append((dsn, kwargs))
        return self.connections["reader" if "_reader" in dsn else "writer"]

    def run_readback(self, **overrides):
        options = {**self.options, **overrides}
        return readback.verify_runtime(
            **options, environ=self.env,
            _dependencies=(self.connect, lambda dsn: dict(piece.split("=", 1) for piece in shlex.split(dsn)),
                           lambda: MIGRATIONS.copy(), lambda values: SCHEMA if values == MIGRATIONS else "invalid"))

    def assert_blocked(self, code, **overrides):
        with self.assertRaises(readback.RuntimeReadbackError) as caught:
            self.run_readback(**overrides)
        self.assertEqual(caught.exception.code, code)

    def test_installed_populated_target_has_two_read_only_observations(self):
        result = self.run_readback()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["roles"], {kind: DATABASE + "_" + kind for kind in ("reader", "writer")})
        self.assertEqual(len(self.connects), 2)
        self.assertTrue(all(event[2] for event in self.events))
        self.assertTrue(all(connection.populated_study_rows for connection in self.connections.values()))
        self.assertTrue(result["read_only"])
        self.assertRegex(result["evidenceId"], r"^sha256:[0-9a-f]{64}$")

    def test_physical_system_identity_is_explicitly_caller_bound(self):
        result = self.run_readback()
        self.assertEqual(result["server"], {"hostaddr": "192.0.2.7", "port": 5432,
                                           "system_identifier": "123456789", "identity_scope": "caller-bound"})

    def test_both_connections_force_endpoint_and_private_passfile(self):
        self.run_readback()
        for _, kwargs in self.connects:
            self.assertEqual(kwargs["hostaddr"], "192.0.2.7")
            self.assertEqual(kwargs["port"], 5432)
            self.assertEqual(kwargs["passfile"], str(self.passfile))
            self.assertFalse(kwargs["autocommit"])
            self.assertEqual(kwargs["connect_timeout"], 2)
            self.assertIn("default_transaction_read_only=on", kwargs["options"])

    def test_connection_authority_mismatch_blocks_without_opening_connections(self):
        for field, value in (("user", "admin"), ("dbname", "other"), ("port", "5433")):
            with self.subTest(field=field):
                self.env["TEMPO_LEARNING_DATABASE_URL"] = (
                    f"host=postgres port=5432 dbname={DATABASE} user={DATABASE}_writer " + field + "=" + value)
                self.assert_blocked("runtime_connection_identity_mismatch")
                self.assertEqual(self.connects, [])

    def test_secret_options_service_and_hostaddr_are_not_embedded_in_dsns(self):
        for extra in ("password=synthetic-secret", "options=-crole=admin", "service=fixture", "hostaddr=192.0.2.8", "passfile=/elsewhere"):
            with self.subTest(extra=extra):
                original = self.env["TEMPO_LEARNING_DATABASE_URL"]
                self.env["TEMPO_LEARNING_DATABASE_URL"] = original + " " + extra
                self.assert_blocked("runtime_connection_invalid")
                self.env["TEMPO_LEARNING_DATABASE_URL"] = original
        self.assertEqual(self.connects, [])

    def test_libpq_environment_override_refuses_before_any_connection(self):
        for key in ("PGHOSTADDR", "PGSERVICE", "PGPASSWORD", "PGOPTIONS", "PGUSER"):
            with self.subTest(key=key):
                self.env[key] = "synthetic-untrusted"
                self.assert_blocked("libpq_override_forbidden")
                self.env.pop(key)
        self.assertEqual(self.connects, [])

    def test_unix_or_multi_host_cannot_override_observed_endpoint(self):
        for host in ("/tmp", "@socket", "a,b"):
            self.env["TEMPO_LEARNING_DATABASE_URL"] = f"host={host} port=5432 dbname={DATABASE} user={DATABASE}_writer"
            self.assert_blocked("runtime_connection_invalid")
        self.assertEqual(self.connects, [])

    def test_private_passfile_is_required_and_symlinks_are_refused(self):
        self.passfile.chmod(0o644)
        self.assert_blocked("private_passfile_required")
        self.passfile.chmod(0o600)
        alias = self.passfile.with_name("alias")
        alias.symlink_to(self.passfile)
        self.env["PGPASSFILE"] = str(alias)
        self.assert_blocked("private_passfile_required")
        self.assertEqual(self.connects, [])

    def test_selected_installed_schema_must_match_before_connections(self):
        self.assert_blocked("selected_source_schema_mismatch", expected_schema="sha256:" + "c" * 64)
        self.assertEqual(self.connects, [])

    def test_reader_and_writer_must_observe_exact_migration_map(self):
        for kind in ("reader", "writer"):
            with self.subTest(kind=kind):
                original = self.connections[kind].migrations
                self.connections[kind].migrations = [("fixture-migration", "c" * 64)]
                self.assert_blocked("runtime_schema_mismatch")
                self.connections[kind].migrations = original

    def test_duplicate_or_additional_migration_rows_are_refused(self):
        self.connections["reader"].migrations *= 2
        self.assert_blocked("runtime_schema_mismatch")

    def test_workspace_and_timezone_mismatch_never_rebinds_database(self):
        for metadata in ([("other-workspace", "America/New_York")], [("fixture-workspace", "UTC")], []):
            self.connections["reader"].metadata = metadata
            self.assert_blocked("runtime_workspace_mismatch")

    def test_session_database_role_endpoint_and_read_only_are_checked(self):
        for attribute, value in (("database", "other"), ("role", "admin"), ("hostaddr", "192.0.2.8"),
                                 ("port", 5433), ("transaction_read_only", "off")):
            with self.subTest(attribute=attribute):
                connection = self.connections["reader"]
                original = getattr(connection, attribute)
                setattr(connection, attribute, value)
                self.assert_blocked("runtime_session_identity_mismatch")
                setattr(connection, attribute, original)

    def test_privileged_inheriting_or_schema_creating_roles_are_refused(self):
        for index in range(1, 8):
            flags = list(self.connections["writer"].flags)
            flags[index] = True
            self.connections["writer"].flags = tuple(flags)
            self.assert_blocked("runtime_role_not_restricted")
            self.connections["writer"].flags = (True, False, False, False, False, False, False, False)

    def test_role_membership_cannot_hide_owner_authority(self):
        self.connections["writer"].memberships = [(DATABASE + "_owner",)]
        self.assert_blocked("runtime_role_membership_unexpected")

    def test_driver_read_only_option_does_not_mask_missing_reader_role_default(self):
        self.connections["reader"].defaults = []
        self.assert_blocked("runtime_role_read_only_default_mismatch")

    def test_reader_write_grants_are_refused_without_attempting_writes(self):
        self.connections["reader"].reader_writes = [("cards",)]
        self.assert_blocked("reader_write_privilege_unexpected")

    def test_driver_failure_is_sanitized(self):
        def unavailable(*args, **kwargs):
            raise OSError("synthetic-private-password must not escape")
        self.connect = unavailable
        with self.assertRaises(readback.RuntimeReadbackError) as caught:
            self.run_readback()
        self.assertEqual(str(caught.exception), "runtime_connection_unavailable")

    def test_expected_configuration_requires_exact_timezone_and_workspace(self):
        self.env["TEMPO_LEARNING_TIMEZONE"] = "UTC"
        self.assert_blocked("runtime_configuration_mismatch")
        self.assertEqual(self.connects, [])

    def test_invalid_physical_or_schema_inputs_fail_before_connections(self):
        for overrides, code in (({"expected_database": "postgres"}, "expected_database_invalid"),
                                ({"expected_system_identifier": "0"}, "expected_system_identifier_invalid"),
                                ({"expected_system_identifier": str(2 ** 64)}, "expected_system_identifier_invalid"),
                                ({"expected_schema": 4}, "expected_schema_invalid"),
                                ({"expected_hostaddr": "postgres"}, "expected_endpoint_or_timezone_invalid"),
                                ({"expected_port": True}, "expected_port_invalid")):
            self.assert_blocked(code, **overrides)
        self.assertEqual(self.connects, [])

    def test_help_is_available_without_driver_or_database_connection(self):
        result = subprocess.run([sys.executable, "-I", str(MODULE_PATH), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--expected-hostaddr", result.stdout)

    def test_cli_error_output_does_not_echo_driver_or_secret_payload(self):
        arguments = []
        for key, value in self.options.items():
            arguments += ["--" + key.replace("_", "-"), str(value)]
        output = StringIO()
        with patch.object(readback, "verify_runtime", side_effect=OSError("synthetic-secret")), redirect_stdout(output):
            self.assertEqual(readback.main(arguments), 1)
        self.assertEqual(json.loads(output.getvalue()), {"contract_version": 1, "status": "blocked", "code": "runtime_readback_unavailable"})


if __name__ == "__main__":
    unittest.main()
