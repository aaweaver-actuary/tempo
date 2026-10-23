import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit


TOOLING_DIRECTORY = Path(__file__).resolve().parents[1] / "tempo_learning_database" / "tooling"
sys.path.insert(0, str(TOOLING_DIRECTORY))

import bootstrap_disposable as bootstrap_module


ADMIN_URL = "postgresql://postgres:admin-secret@postgres:5432/postgres?connect_timeout=2"
INVOCATION_ID = "a1b2c3d4e5f60718"
PROJECT = "tempo-study-test-" + INVOCATION_ID
DATABASE = "learning_study_test_" + INVOCATION_ID
SYSTEM_IDENTIFIER = "7310264387102334211"
OWNERSHIP_RECEIPT = {
    "schema_version": 1,
    "invocation_id": INVOCATION_ID,
    "project_name": PROJECT,
    "database": DATABASE,
    "container_id": "c" * 64,
    "volume_name": PROJECT + "_postgres-data",
    "network_id": "d" * 64,
    "network_name": PROJECT + "_default",
    "service": "postgres",
    "host": "postgres",
    "port": 5432,
    "system_identifier": SYSTEM_IDENTIFIER,
}
WRITER_PASSWORD = "writer:secret\\with-backslash-" + "w" * 32
READER_PASSWORD = "reader:secret\\with-backslash-" + "r" * 32
STUDY_TOKEN = "study-token-" + "s" * 36
OPERATOR_TOKEN = "operator-token-" + "o" * 36
SECRETS = [WRITER_PASSWORD, READER_PASSWORD, STUDY_TOKEN, OPERATOR_TOKEN]


class FakeCursor:
    def __init__(self, one=None, rows=()):
        self.one = one
        self.rows = rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, events, identity, database_exists=None, existing_roles=()):
        self.events = events
        self.identity = identity
        self.database_exists = database_exists
        self.existing_roles = existing_roles
        self.first_sql = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, parameters=()):
        if self.first_sql is None:
            self.first_sql = query
            self.events.append("cluster_identity")
            if (
                "pg_control_system()" not in query
                or "current_database()" not in query
                or "session_user" not in query
            ):
                raise AssertionError("The cluster identity query must be the first SQL action")
            return FakeCursor(one=self.identity)
        if "pg_database" in query:
            self.events.append("database_preflight")
            return FakeCursor(one=self.database_exists)
        if "pg_roles" in query:
            self.events.append("role_preflight")
            return FakeCursor(rows=self.existing_roles)
        raise AssertionError("Unexpected SQL in the mocked bootstrap preflight")


class DisposableBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.admin_file = self.root / "admin.url"
        self.admin_file.write_text(ADMIN_URL, encoding="utf-8")
        self.admin_file.chmod(0o600)
        self.receipt_file = self.root / "ownership.json"
        self.receipt_file.write_text("mocked by the loader seam", encoding="utf-8")
        self.receipt_file.chmod(0o600)
        self.output = self.root / "output"
        self.events = []
        self.database_exists = None
        self.existing_roles = ()
        self.identity = (SYSTEM_IDENTIFIER, "postgres", "postgres")
        self.receipt = OWNERSHIP_RECEIPT
        self.provision_calls = []
        self.migrate_calls = []
        self.grant_calls = []
        self.receipt_calls = []
        self.connect_calls = []

        def connect(url, **kwargs):
            self.events.append("connect")
            self.connect_calls.append((url, kwargs))
            return FakeConnection(
                self.events,
                identity=self.identity,
                database_exists=self.database_exists,
                existing_roles=self.existing_roles,
            )

        self.psycopg = SimpleNamespace(connect=connect)

        def conninfo_to_dict(url):
            parts = urlsplit(url)
            fields = {
                "host": parts.hostname,
                "port": str(parts.port),
                "dbname": parts.path.lstrip("/"),
                "user": parts.username,
                "password": parts.password,
            }
            fields.update(dict(parse_qsl(parts.query, keep_blank_values=True)))
            return fields

        def make_conninfo(conninfo=None, **fields):
            if conninfo is not None:
                return conninfo + " " + " ".join(
                    f"{name}={value}" for name, value in fields.items()
                )
            return " ".join(f"{name}={value}" for name, value in fields.items())

        self.conninfo_to_dict = conninfo_to_dict
        self.make_conninfo = make_conninfo

        def load_receipt(path, *, database):
            self.events.append("ownership_receipt")
            self.receipt_calls.append((Path(path), database))
            return self.receipt

        self.load_receipt = load_receipt

        def provision(*arguments):
            self.events.append("provision")
            self.provision_calls.append(arguments)

        def migrate(*arguments):
            self.events.append("migrate")
            self.migrate_calls.append(arguments)

        def grant_runtime(*arguments):
            self.events.append("grant_runtime")
            self.grant_calls.append(arguments)

        self.owner_apis = (provision, migrate, grant_runtime)

    def tearDown(self):
        self.temporary.cleanup()

    def patched_dependencies(self, secrets=SECRETS, owner_apis=None, loader=None):
        owner_apis = self.owner_apis if owner_apis is None else owner_apis
        loader = self.load_receipt if loader is None else loader
        return (
            patch.object(
                bootstrap_module,
                "_load_driver",
                return_value=(self.psycopg, self.conninfo_to_dict, self.make_conninfo),
            ),
            patch.object(bootstrap_module, "_load_owner_apis", return_value=owner_apis),
            patch.object(bootstrap_module, "_load_ownership_receipt", side_effect=loader),
            patch.object(bootstrap_module, "_generate_secret", side_effect=secrets),
        )

    def do_bootstrap(self):
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            bootstrap_module.bootstrap(
                admin_url_file=self.admin_file,
                ownership_receipt_file=self.receipt_file,
                database=DATABASE,
                workspace="workspace-a",
                timezone_name="America/New_York",
                output_directory=self.output,
            )

    def test_exact_cli_receipt_loader_and_owner_order(self):
        options = {
            value
            for action in bootstrap_module._parser()._actions
            for value in action.option_strings
        }
        self.assertEqual(
            options,
            {
                "-h",
                "--help",
                "--admin-url-file",
                "--ownership-receipt-file",
                "--database",
                "--workspace",
                "--timezone",
                "--output-directory",
            },
        )
        self.do_bootstrap()
        self.assertEqual(self.receipt_calls, [(self.receipt_file, DATABASE)])
        self.assertEqual(
            self.events,
            [
                "ownership_receipt",
                "connect",
                "cluster_identity",
                "database_preflight",
                "role_preflight",
                "provision",
                "migrate",
                "grant_runtime",
            ],
        )
        self.assertEqual(self.connect_calls[0][1], {"autocommit": True})

    def test_cluster_identity_is_first_sql_and_mismatch_prevents_mutation(self):
        for identity in (
            ("999", "postgres", "postgres"),
            (SYSTEM_IDENTIFIER, "wrong_database", "postgres"),
            (SYSTEM_IDENTIFIER, "postgres", "wrong_user"),
        ):
            with self.subTest(identity=identity):
                self.identity = identity
                self.events.clear()
                patches = self.patched_dependencies()
                with patches[0], patches[1], patches[2], patches[3]:
                    with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                        bootstrap_module.bootstrap(
                            admin_url_file=self.admin_file,
                            ownership_receipt_file=self.receipt_file,
                            database=DATABASE,
                            workspace="workspace-a",
                            timezone_name="America/New_York",
                            output_directory=self.output,
                        )
                self.assertEqual(raised.exception.code, "database_identity_mismatch")
                self.assertEqual(
                    self.events,
                    ["ownership_receipt", "connect", "cluster_identity"],
                )
        self.assertEqual(self.provision_calls, [])
        self.assertEqual(self.migrate_calls, [])
        self.assertEqual(self.grant_calls, [])
        self.assertFalse(self.output.exists())

    def test_ownership_error_code_is_reported_without_connecting(self):
        class FakeOwnershipError(Exception):
            code = "receipt_mode_invalid"

        def rejected_receipt(path, *, database):
            self.events.append("ownership_receipt")
            raise FakeOwnershipError("untrusted secret details")

        patches = self.patched_dependencies(loader=rejected_receipt)
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    ownership_receipt_file=self.receipt_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                )
        self.assertEqual(raised.exception.code, "ownership_receipt_mode_invalid")
        self.assertEqual(self.events, ["ownership_receipt"])
        self.assertFalse(self.output.exists())

    def test_owner_apis_run_once_in_order_and_emit_exact_private_schema(self):
        self.do_bootstrap()
        self.assertEqual(len(self.provision_calls), 1)
        self.assertEqual(len(self.migrate_calls), 1)
        self.assertEqual(len(self.grant_calls), 1)

        admin_url, database, owner, writer, reader, writer_password, reader_password = self.provision_calls[0]
        self.assertIn("host=postgres", admin_url)
        self.assertIn("port=5432", admin_url)
        self.assertIn("dbname=postgres", admin_url)
        self.assertIn("user=postgres", admin_url)
        self.assertIn("password=admin-secret", admin_url)
        self.assertIn("options=", admin_url)
        self.assertEqual(database, DATABASE)
        self.assertEqual(owner, DATABASE + "_owner")
        self.assertEqual(writer, DATABASE + "_writer")
        self.assertEqual(reader, DATABASE + "_reader")
        self.assertEqual((writer_password, reader_password), (WRITER_PASSWORD, READER_PASSWORD))
        self.assertNotEqual(writer_password, reader_password)

        owner_url, workspace, timezone_name = self.migrate_calls[0]
        self.assertIn("options=-c role=" + owner, owner_url)
        self.assertEqual((workspace, timezone_name), ("workspace-a", "America/New_York"))
        self.assertEqual(self.grant_calls, [(owner_url, writer, reader)])

        self.assertEqual({path.name for path in self.output.iterdir()}, set(bootstrap_module._OUTPUT_FILES))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for name in bootstrap_module._OUTPUT_FILES:
            self.assertEqual(stat.S_IMODE((self.output / name).stat().st_mode), 0o600)

        manifest_path = self.output / "connection.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {
                "schema_version",
                "database",
                "workspace_id",
                "timezone",
                "owner_role",
                "writer_role",
                "reader_role",
                "writer_dsn",
                "reader_dsn",
                "pgpass_file",
                "study_token_file",
                "operator_token_file",
            },
        )
        self.assertEqual(
            {key: manifest[key] for key in (
                "schema_version", "database", "workspace_id", "timezone",
                "owner_role", "writer_role", "reader_role", "pgpass_file",
                "study_token_file", "operator_token_file",
            )},
            {
                "schema_version": 1,
                "database": DATABASE,
                "workspace_id": "workspace-a",
                "timezone": "America/New_York",
                "owner_role": owner,
                "writer_role": writer,
                "reader_role": reader,
                "pgpass_file": "runtime.pgpass",
                "study_token_file": "study.token",
                "operator_token_file": "operator.token",
            },
        )
        for dsn, expected_role in ((manifest["writer_dsn"], writer), (manifest["reader_dsn"], reader)):
            self.assertEqual(
                {item.split("=", 1)[0] for item in dsn.split()},
                {"host", "port", "dbname", "user"},
            )
            self.assertIn("host=postgres", dsn)
            self.assertIn("port=5432", dsn)
            self.assertIn("dbname=" + DATABASE, dsn)
            self.assertIn("user=" + expected_role, dsn)
            self.assertNotIn("password", dsn)
            self.assertNotIn("admin-secret", dsn)
            self.assertNotIn(owner, dsn)

        expected_passfile = (
            f"postgres:5432:{DATABASE}:{writer}:writer\\:secret\\\\with-backslash-{'w' * 32}\n"
            f"postgres:5432:{DATABASE}:{reader}:reader\\:secret\\\\with-backslash-{'r' * 32}\n"
        )
        self.assertEqual((self.output / "runtime.pgpass").read_text(), expected_passfile)
        self.assertEqual((self.output / "study.token").read_text(), STUDY_TOKEN + "\n")
        self.assertEqual((self.output / "operator.token").read_text(), OPERATOR_TOKEN + "\n")
        self.assertNotEqual(STUDY_TOKEN, OPERATOR_TOKEN)
        serialized_manifest = manifest_path.read_text(encoding="utf-8")
        for secret in (ADMIN_URL, WRITER_PASSWORD, READER_PASSWORD, STUDY_TOKEN, OPERATOR_TOKEN, SYSTEM_IDENTIFIER):
            self.assertNotIn(secret, serialized_manifest)

    def test_admin_url_routing_overrides_are_rejected(self):
        for key, value in (("service", "other"), ("hostaddr", "127.0.0.2"), ("options", "-c role=other"), ("passfile", "/tmp/other")):
            with self.subTest(key=key):
                admin_file = self.root / (key + ".url")
                admin_file.write_text(ADMIN_URL + "&" + key + "=" + value, encoding="utf-8")
                admin_file.chmod(0o600)
                patches = self.patched_dependencies()
                with patches[0], patches[1], patches[2], patches[3]:
                    with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                        bootstrap_module.bootstrap(
                            admin_url_file=admin_file,
                            ownership_receipt_file=self.receipt_file,
                            database=DATABASE,
                            workspace="workspace-a",
                            timezone_name="America/New_York",
                            output_directory=self.output,
                        )
                self.assertEqual(raised.exception.code, "admin_url_override_forbidden")
                self.assertNotIn("connect", self.events)
                self.assertEqual(self.provision_calls, [])

    def test_existing_database_or_any_role_stops_after_identity_check(self):
        self.database_exists = (1,)
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    ownership_receipt_file=self.receipt_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                )
        self.assertEqual(raised.exception.code, "target_already_exists")
        self.assertEqual(self.events, ["ownership_receipt", "connect", "cluster_identity", "database_preflight", "role_preflight"])
        self.assertEqual(self.provision_calls, [])
        self.assertFalse(self.output.exists())

        self.events.clear()
        self.database_exists = None
        self.existing_roles = [(DATABASE + "_reader",)]
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    ownership_receipt_file=self.receipt_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                )
        self.assertEqual(raised.exception.code, "target_already_exists")
        self.assertEqual(self.provision_calls, [])
        self.assertFalse(self.output.exists())

    def test_invalid_name_and_nonempty_output_are_rejected_before_connect(self):
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    ownership_receipt_file=self.receipt_file,
                    database="learning_study_test_",
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                )
        self.assertEqual(raised.exception.code, "database_name_invalid")
        self.assertEqual(self.events, [])

        self.output.mkdir(mode=0o700)
        (self.output / "keep.txt").write_text("existing", encoding="utf-8")
        self.events.clear()
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    ownership_receipt_file=self.receipt_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                )
        self.assertEqual(raised.exception.code, "output_directory_not_empty")
        self.assertEqual(self.events, ["ownership_receipt"])
        self.assertNotIn("connect", self.events)
        self.assertEqual((self.output / "keep.txt").read_text(), "existing")

    def test_partial_migration_failure_redacts_driver_message_and_stops(self):
        failure_text = ADMIN_URL + " " + WRITER_PASSWORD + " " + READER_PASSWORD

        def fail_migrate(*arguments):
            self.events.append("migrate")
            self.migrate_calls.append(arguments)
            raise RuntimeError(failure_text)

        owner_apis = (self.owner_apis[0], fail_migrate, self.owner_apis[2])
        patches = self.patched_dependencies(owner_apis=owner_apis)
        stderr = io.StringIO()
        with patches[0], patches[1], patches[2], patches[3], redirect_stderr(stderr):
            result = bootstrap_module.main(
                [
                    "--admin-url-file", str(self.admin_file),
                    "--ownership-receipt-file", str(self.receipt_file),
                    "--database", DATABASE,
                    "--workspace", "workspace-a",
                    "--timezone", "America/New_York",
                    "--output-directory", str(self.output),
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(self.events.count("provision"), 1)
        self.assertEqual(self.events.count("migrate"), 1)
        self.assertNotIn("grant_runtime", self.events)
        message = stderr.getvalue()
        for secret in (ADMIN_URL, "admin-secret", WRITER_PASSWORD, READER_PASSWORD, STUDY_TOKEN, OPERATOR_TOKEN):
            self.assertNotIn(secret, message)
        for identity in (DATABASE, DATABASE + "_owner", DATABASE + "_writer", DATABASE + "_reader"):
            self.assertIn(identity, message)
        self.assertFalse(self.output.exists())

    def test_admin_url_file_must_be_private(self):
        self.admin_file.chmod(0o640)
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    ownership_receipt_file=self.receipt_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                )
        self.assertEqual(raised.exception.code, "admin_url_file_permissions")
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
