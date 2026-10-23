import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit


TOOLING_DIRECTORY = Path(__file__).resolve().parents[1] / "tempo_learning_database" / "tooling"
sys.path.insert(0, str(TOOLING_DIRECTORY))

import bootstrap_disposable as bootstrap_module


ADMIN_URL = "postgresql://postgres:admin-secret@127.0.0.1:55433/postgres"
DATABASE = "learning_study_test_0123456789ab"
RUNNER_ID = "tempo-learning-a1b2c3d4e5f60718"
RUNNER_ENVIRONMENT = {
    "TEMPO_LEARNING_TEST_FIXTURE": RUNNER_ID,
    "TEMPO_LEARNING_TEST_ADMIN": ADMIN_URL,
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
    def __init__(self, events, database_exists=None, existing_roles=()):
        self.events = events
        self.database_exists = database_exists
        self.existing_roles = existing_roles

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, parameters=()):
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
        self.output = self.root / "output"
        self.events = []
        self.database_exists = None
        self.existing_roles = ()
        self.provision_calls = []
        self.migrate_calls = []
        self.grant_calls = []

        def connect(url):
            self.events.append("connect")
            self.connected_url = url
            return FakeConnection(
                self.events,
                database_exists=self.database_exists,
                existing_roles=self.existing_roles,
            )

        self.psycopg = SimpleNamespace(connect=connect)

        def conninfo_to_dict(url):
            parts = urlsplit(url)
            return {
                "host": parts.hostname,
                "port": str(parts.port),
                "dbname": parts.path.lstrip("/"),
                "user": parts.username,
                "password": parts.password,
            }

        def make_conninfo(conninfo=None, **fields):
            if conninfo is not None:
                return conninfo + " " + " ".join(
                    f"{name}={value}" for name, value in fields.items()
                )
            return " ".join(f"{name}={value}" for name, value in fields.items())

        self.conninfo_to_dict = conninfo_to_dict
        self.make_conninfo = make_conninfo

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

        def inspect_owned_server(identity):
            self.events.append("inspect_owned_server")
            self.inspected_identity = identity
            return self.container(), self.volume()

        self.inspect_owned_server = inspect_owned_server

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def container(labels=None, host_port="55433"):
        labels = labels or {
            "tempo.owner": RUNNER_ID,
            "tempo.disposable": "true",
        }
        return {
            "Name": "/" + RUNNER_ID,
            "Config": {"Labels": labels},
            "State": {"Running": True},
            "NetworkSettings": {
                "Ports": {
                    "5432/tcp": [
                        {"HostIp": "127.0.0.1", "HostPort": host_port}
                    ]
                }
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": RUNNER_ID + "-data",
                    "Destination": "/var/lib/postgresql",
                }
            ],
        }

    @staticmethod
    def volume(labels=None):
        labels = labels or {
            "tempo.owner": RUNNER_ID,
            "tempo.disposable": "true",
        }
        return {"Name": RUNNER_ID + "-data", "Labels": labels}

    def patched_dependencies(self, secrets=SECRETS, owner_apis=None, inspector=None):
        owner_apis = self.owner_apis if owner_apis is None else owner_apis
        inspector = self.inspect_owned_server if inspector is None else inspector
        return (
            patch.object(
                bootstrap_module,
                "_load_driver",
                return_value=(self.psycopg, self.conninfo_to_dict, self.make_conninfo),
            ),
            patch.object(bootstrap_module, "_load_owner_apis", return_value=owner_apis),
            patch.object(bootstrap_module, "_inspect_owned_server", side_effect=inspector),
            patch.object(bootstrap_module, "_generate_secret", side_effect=secrets),
        )

    def do_bootstrap(self, *, environment=None):
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            bootstrap_module.bootstrap(
                admin_url_file=self.admin_file,
                database=DATABASE,
                workspace="workspace-a",
                timezone_name="America/New_York",
                output_directory=self.output,
                runner_environment=(
                    RUNNER_ENVIRONMENT if environment is None else environment
                ),
            )

    def test_exact_cli_and_runner_resource_ownership_contract(self):
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
                "--database",
                "--workspace",
                "--timezone",
                "--output-directory",
            },
        )
        self.do_bootstrap()
        self.assertEqual(self.inspected_identity, RUNNER_ID)
        self.assertEqual(self.events[0], "inspect_owned_server")
        self.assertEqual(
            self.events,
            [
                "inspect_owned_server",
                "connect",
                "database_preflight",
                "role_preflight",
                "provision",
                "migrate",
                "grant_runtime",
            ],
        )

    def test_name_prefix_alone_is_rejected_before_database_connection(self):
        environment = {
            "TEMPO_LEARNING_TEST_FIXTURE": "tempo-learning-",
            "TEMPO_LEARNING_TEST_ADMIN": ADMIN_URL,
        }
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2] as inspect, patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=environment,
                )
        self.assertEqual(raised.exception.code, "runner_ownership_missing")
        self.assertEqual(self.events, [])
        inspect.assert_not_called()

    def test_runner_identity_must_match_container_and_volume_labels(self):
        bad_container = self.container(
            labels={"tempo.owner": "tempo-learning-other", "tempo.disposable": "true"}
        )
        patches = self.patched_dependencies(
            inspector=lambda _identity: (bad_container, self.volume())
        )
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=RUNNER_ENVIRONMENT,
                )
        self.assertEqual(raised.exception.code, "runner_ownership_unverified")
        self.assertNotIn("connect", self.events)

    def test_owner_apis_run_once_in_order_and_emit_exact_private_schema(self):
        self.do_bootstrap()
        self.assertEqual(len(self.provision_calls), 1)
        self.assertEqual(len(self.migrate_calls), 1)
        self.assertEqual(len(self.grant_calls), 1)

        admin_url, database, owner, writer, reader, writer_password, reader_password = self.provision_calls[0]
        self.assertEqual(admin_url, ADMIN_URL)
        self.assertEqual(database, DATABASE)
        self.assertEqual(owner, DATABASE + "_owner")
        self.assertEqual(writer, DATABASE + "_writer")
        self.assertEqual(reader, DATABASE + "_reader")
        self.assertEqual((writer_password, reader_password), (WRITER_PASSWORD, READER_PASSWORD))
        self.assertNotEqual(writer_password, reader_password)

        owner_url, workspace, timezone_name = self.migrate_calls[0]
        self.assertIn("admin-secret", owner_url)
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
            self.assertIn("host=127.0.0.1", dsn)
            self.assertIn("port=55433", dsn)
            self.assertIn("dbname=" + DATABASE, dsn)
            self.assertIn("user=" + expected_role, dsn)
            self.assertNotIn("password", dsn)
            self.assertNotIn("admin-secret", dsn)
            self.assertNotIn(owner, dsn)

        expected_passfile = (
            f"127.0.0.1:55433:{DATABASE}:{writer}:writer\\:secret\\\\with-backslash-{'w' * 32}\n"
            f"127.0.0.1:55433:{DATABASE}:{reader}:reader\\:secret\\\\with-backslash-{'r' * 32}\n"
        )
        self.assertEqual((self.output / "runtime.pgpass").read_text(), expected_passfile)
        self.assertEqual((self.output / "study.token").read_text(), STUDY_TOKEN + "\n")
        self.assertEqual((self.output / "operator.token").read_text(), OPERATOR_TOKEN + "\n")
        self.assertNotEqual(STUDY_TOKEN, OPERATOR_TOKEN)
        serialized_manifest = manifest_path.read_text(encoding="utf-8")
        for secret in (ADMIN_URL, WRITER_PASSWORD, READER_PASSWORD, STUDY_TOKEN, OPERATOR_TOKEN):
            self.assertNotIn(secret, serialized_manifest)

    def test_existing_database_or_any_role_stops_before_owner_apis(self):
        self.database_exists = (1,)
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=RUNNER_ENVIRONMENT,
                )
        self.assertEqual(raised.exception.code, "target_already_exists")
        self.assertEqual(self.provision_calls, [])
        self.assertEqual(self.migrate_calls, [])
        self.assertEqual(self.grant_calls, [])
        self.assertFalse(self.output.exists())

        self.database_exists = None
        self.existing_roles = [(DATABASE + "_reader",)]
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=RUNNER_ENVIRONMENT,
                )
        self.assertEqual(raised.exception.code, "target_already_exists")
        self.assertEqual(self.provision_calls, [])
        self.assertFalse(self.output.exists())

    def test_invalid_name_and_nonempty_output_are_rejected_before_connection(self):
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    database="learning_study_test_",
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=RUNNER_ENVIRONMENT,
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
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=RUNNER_ENVIRONMENT,
                )
        self.assertEqual(raised.exception.code, "output_directory_not_empty")
        self.assertNotIn("connect", self.events)
        self.assertEqual(self.events, ["inspect_owned_server"])
        self.assertEqual(self.provision_calls, [])
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
        with patches[0], patches[1], patches[2], patches[3], patch.dict(
            os.environ, RUNNER_ENVIRONMENT, clear=True
        ), redirect_stderr(stderr):
            result = bootstrap_module.main(
                [
                    "--admin-url-file", str(self.admin_file),
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

    def test_admin_url_file_must_be_private_and_a_regular_file(self):
        self.admin_file.chmod(0o640)
        patches = self.patched_dependencies()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(bootstrap_module.BootstrapError) as raised:
                bootstrap_module.bootstrap(
                    admin_url_file=self.admin_file,
                    database=DATABASE,
                    workspace="workspace-a",
                    timezone_name="America/New_York",
                    output_directory=self.output,
                    runner_environment=RUNNER_ENVIRONMENT,
                )
        self.assertEqual(raised.exception.code, "admin_url_file_permissions")
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
