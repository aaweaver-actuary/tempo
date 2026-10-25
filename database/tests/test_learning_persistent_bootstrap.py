"""Persistent bootstrap semantic fakes; no real PostgreSQL, Docker or target."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

DATABASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATABASE_ROOT / "tempo_learning_database" / "tooling"))
import bootstrap_persistent as bootstrap

ADMIN = "postgresql://admin:synthetic-admin-secret@postgres:5432/postgres"


def intent_fixture():
    return {
        "kind": bootstrap.INTENT_KIND, "schema_version": 1,
        "transition_id": "synthetic-persistent-transition", "source_revision": "a" * 40,
        "target_identity": "synthetic-explicit-launcher-target", "project": "synthetic-persistent",
        "database": "tempo_persistent_fixture", "workspace_id": "synthetic-fresh-workspace",
        "timezone": "America/New_York", "ingress_origin": "http://127.0.0.1:38127",
        "context": "synthetic-context", "daemon_id": "synthetic-daemon", "host": "postgres", "port": 5432,
        "data_volume": "synthetic-new-persistent-data", "runtime_secrets_volume": "synthetic-new-private-files",
        "network_name": "synthetic-persistent-network", "source_volume_names": ["synthetic-preserved-source"],
        "images": {"postgres": "sha256:" + "1" * 64, "learning": "sha256:" + "2" * 64,
                   "study-web": "sha256:" + "3" * 64},
    }


def resource_fixture(intent):
    return {
        "kind": bootstrap.RESOURCE_KIND, "schema_version": 1, "evidence_scope": "synthetic-fixture",
        "evidence_id": "synthetic-resource-observation", "intent_sha256": bootstrap._digest(intent),
        "observed_at": "2026-10-25T20:00:00Z",
        **{key: intent[key] for key in bootstrap._BINDING},
        "container_id": "c" * 64, "network_id": "d" * 64, "system_identifier": "123456789",
        "labels": {"tempo.owner": intent["project"], "tempo.target-kind": "persistent", "tempo.disposable": "false"},
        "new_data_volume": True, "new_runtime_secrets_volume": True,
        "postgres_image": intent["images"]["postgres"],
    }


def fake_schema_identity(value):
    return "sha256:" + bootstrap._digest(value)


class FakeOwner:
    def __init__(self, fail=None):
        self.events = []
        self.fail = fail
        self.applied = {"learning.runtime/synthetic": "a" * 64}
    def action(self, name, *args, **kwargs):
        self.events.append((name, args, kwargs))
        if self.fail == name:
            raise RuntimeError("synthetic-admin-secret must never escape")
    def inspect_persistent_target(self, *args, **kwargs):
        self.action("inspect", *args, **kwargs)
    def provision_persistent(self, *args, **kwargs):
        self.action("provision", *args, **kwargs)
    def verify_new_persistent_database(self, *args, **kwargs):
        self.action("empty", *args, **kwargs)
    def migrate(self, *args, **kwargs):
        self.action("migrate", *args, **kwargs)
    def grant_runtime(self, *args, **kwargs):
        self.action("grant", *args, **kwargs)
    def verify_persistent_initialized(self, *args, **kwargs):
        self.action("verify", *args, **kwargs)
        return {"expected_migrations": self.applied, "applied_migrations": self.applied,
                "identity": self.schema_identity(self.applied), "migration_count": len(self.applied)}
    def expected_migrations(self):
        return dict(self.applied)
    schema_identity = staticmethod(fake_schema_identity)


def parse_admin(value):
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    return {"host": parsed.hostname, "port": str(parsed.port), "dbname": parsed.path.lstrip("/"),
            "user": parsed.username, "password": parsed.password}


def make_conninfo(value=None, **fields):
    return (value + " " if value else "") + " ".join(str(k) + "=" + str(v) for k, v in fields.items())


class PersistentPlanningTests(unittest.TestCase):
    def test_exact_persistent_contract_and_immutable_plan_copy(self):
        intent = intent_fixture()
        resources = resource_fixture(intent)
        plan = bootstrap.plan_persistent_target(intent, resources)
        self.assertEqual(plan["roles"]["reader"], intent["database"] + "_reader")
        plan["intent"]["workspace_id"] = "changed-copy"
        self.assertNotEqual(intent["workspace_id"], "changed-copy")

    def test_disposable_receipt_cannot_be_relabelled_or_promoted(self):
        intent = intent_fixture()
        resources = resource_fixture(intent)
        for change in (
            {"kind": "tempo-study-disposable-ownership"},
            {"labels": {"tempo.owner": intent["project"], "tempo.target-kind": "persistent", "tempo.disposable": "true"}},
            {"new_data_volume": False},
            {"postgres_image": "postgres:latest"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(bootstrap.PersistentBootstrapError):
                    bootstrap.plan_persistent_target(intent, {**resources, **change})

    def test_existing_source_volume_is_never_reused_for_either_new_volume(self):
        for key in ("data_volume", "runtime_secrets_volume"):
            intent = intent_fixture()
            intent[key] = intent["source_volume_names"][0]
            with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
                bootstrap.plan_persistent_target(intent, resource_fixture(intent))
            self.assertEqual(caught.exception.code, "source_volume_reuse")

    def test_missing_or_mismatched_actual_resource_identity_blocks_plan(self):
        intent = intent_fixture()
        resource = resource_fixture(intent)
        for change in ({"daemon_id": "other-daemon"}, {"system_identifier": ""},
                       {"intent_sha256": "0" * 64}, {"observed_at": "2026-10-25T20:00:00"},
                       {"target_identity": "another-target"}):
            with self.subTest(change=change):
                with self.assertRaises(bootstrap.PersistentBootstrapError):
                    bootstrap.plan_persistent_target(intent, {**resource, **change})

    def test_invalid_database_aliases_and_unpinned_artifacts_fail_closed(self):
        for database in ("postgres", "template1", "learning_study_test_" + "a" * 16, "unsafe-name"):
            intent = intent_fixture()
            intent["database"] = database
            with self.assertRaises(bootstrap.PersistentBootstrapError):
                bootstrap.plan_persistent_target(intent, resource_fixture(intent))
        intent = intent_fixture()
        intent["images"]["learning"] = "learning:latest"
        with self.assertRaises(bootstrap.PersistentBootstrapError):
            bootstrap.plan_persistent_target(intent, resource_fixture(intent))


class PersistentEffectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.admin_file = self.root / "admin.url"
        self.admin_file.write_text(ADMIN)
        self.admin_file.chmod(0o600)
        self.intent = intent_fixture()
        self.resources = resource_fixture(self.intent)
        self.owner = FakeOwner()
        self.clear_env = patch.dict(os.environ, {key: "" for key in bootstrap._PG_OVERRIDES})
        self.clear_env.start()
    def tearDown(self):
        self.clear_env.stop()
        self.temp.cleanup()
    def execute(self, name="new-runtime"):
        return bootstrap.bootstrap_persistent(intent=self.intent, resources=self.resources,
            admin_url_file=self.admin_file, output_directory=self.root / name,
            protected_checkout_root=self.checkout,
            _dependencies=(None, parse_admin, make_conninfo, self.owner))

    def test_owned_creation_order_private_files_and_distinct_nonserving_receipt(self):
        receipt = self.execute()
        self.assertEqual([event[0] for event in self.owner.events],
                         ["inspect", "provision", "empty", "migrate", "grant", "verify"])
        self.assertEqual(receipt["kind"], "tempo-learning-persistent-bootstrap")
        self.assertEqual(receipt["evidence_scope"], "synthetic-fixture")
        self.assertEqual(receipt["status"], "initialized-quarantine-required")
        self.assertEqual(receipt["schema"]["identity"], fake_schema_identity(self.owner.applied))
        self.assertEqual(receipt["schema"]["expected_migrations"], receipt["schema"]["applied_migrations"])
        for name in ("services_started", "maintenance_activated", "public_traffic_activated", "registration_published"):
            self.assertIs(receipt["effects"][name], False)
        for path in (self.root / "new-runtime").iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        manifest = json.loads((self.root / "new-runtime" / "connection.json").read_text())
        self.assertNotIn("password", manifest["writer_dsn"])
        self.assertNotIn("synthetic-admin-secret", json.dumps(receipt))
        self.assertNotIn("graphClosureVerified", json.dumps(receipt))

    def test_returned_receipt_cannot_mutate_future_private_output_contract(self):
        original = dict(bootstrap._OUTPUTS)
        receipt = self.execute()
        receipt["private_outputs"]["passfile"] = "synthetic-redirect"
        self.assertEqual(bootstrap._OUTPUTS, original)
        self.execute("second-new-target-output")
        self.assertTrue((self.root / "second-new-target-output" / "runtime.pgpass").exists())
        self.assertFalse((self.root / "second-new-target-output" / "synthetic-redirect").exists())

    def test_physical_identity_or_existing_target_failure_stops_before_credentials_or_create(self):
        self.owner.fail = "inspect"
        with patch.object(bootstrap.secrets, "token_urlsafe") as generate:
            with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
                self.execute()
        generate.assert_not_called()
        self.assertEqual([e[0] for e in self.owner.events], ["inspect"])
        self.assertNotIn("synthetic-admin-secret", str(caught.exception))

    def test_output_is_never_reused_or_overwritten(self):
        self.execute()
        self.owner.events.clear()
        with self.assertRaises(bootstrap.PersistentBootstrapError):
            self.execute()
        self.assertEqual(self.owner.events, [])

    def test_interruption_preserves_private_state_and_never_drops_or_claims_success(self):
        for failure in ("provision", "empty", "migrate", "grant", "verify"):
            with self.subTest(failure=failure):
                self.owner = FakeOwner(fail=failure)
                with self.assertRaises(bootstrap.PersistentBootstrapError):
                    self.execute(failure)
                directory = self.root / failure
                state = json.loads((directory / "provisioning-state.json").read_text())
                self.assertEqual(state["phase"], "interrupted-or-failed")
                self.assertEqual(state["failed_during"], {"empty": "empty-target", "grant": "grant-runtime"}.get(failure, failure))
                self.assertFalse((directory / "persistent-target.json").exists())
                self.assertTrue((directory / "runtime.pgpass").exists())
                self.assertFalse(any(e[0] in {"drop", "start", "activate", "publish"} for e in self.owner.events))

    def test_interrupt_payload_is_sanitized_and_partial_state_retained(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt("synthetic-admin-secret must not escape")
        self.owner.provision_persistent = interrupted
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.execute()
        self.assertEqual(str(caught.exception), "")
        directory = self.root / "new-runtime"
        state = json.loads((directory / "provisioning-state.json").read_text())
        self.assertEqual(state["phase"], "interrupted-or-failed")
        self.assertEqual(state["failed_during"], "provision")
        self.assertFalse((directory / "persistent-target.json").exists())

    def test_schema_readback_mismatch_refuses_receipt(self):
        def inconsistent(*args, **kwargs):
            return {"expected_migrations": {"a": "a" * 64}, "applied_migrations": {"a": "b" * 64},
                    "identity": "sha256:" + "0" * 64, "migration_count": 1}
        self.owner.verify_persistent_initialized = inconsistent
        with self.assertRaises(bootstrap.PersistentBootstrapError):
            self.execute()
        self.assertFalse((self.root / "new-runtime" / "persistent-target.json").exists())

    def test_durability_error_is_failure_not_initialized_success(self):
        original = bootstrap._sync_directory
        calls = []
        def fail(directory):
            calls.append(directory)
            if len(calls) == 2:
                raise OSError("synthetic unsupported directory fsync")
            return original(directory)
        with patch.object(bootstrap, "_sync_directory", side_effect=fail):
            with self.assertRaises(bootstrap.PersistentBootstrapError):
                self.execute()
        self.assertFalse((self.root / "new-runtime" / "persistent-target.json").exists())
        self.assertEqual(self.owner.events, [])

    def test_runtime_guard_rejects_fixture_receipt_and_enabled_maintenance(self):
        receipt = self.execute()
        directory = self.root / "new-runtime"
        with self.assertRaises(bootstrap.PersistentBootstrapError):
            bootstrap.check_quarantine_receipt(directory / "persistent-target.json", bootstrap._digest(self.intent), bootstrap._digest(self.resources))
        # A synthetic file fixture models the observed protocol; it is not real acceptance.
        receipt["evidence_scope"] = "observed"
        (directory / "persistent-target.json").write_text(json.dumps(receipt))
        state = json.loads((directory / "provisioning-state.json").read_text())
        state["evidence_scope"] = "observed"
        (directory / "provisioning-state.json").write_text(json.dumps(state))
        with patch.dict(os.environ, {"TEMPO_LEARNING_MAINTENANCE": "1"}):
            with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
                bootstrap.check_quarantine_receipt(directory / "persistent-target.json", bootstrap._digest(self.intent), bootstrap._digest(self.resources))
        self.assertEqual(caught.exception.code, "maintenance_must_be_disabled")

    def test_credentials_cannot_be_written_inside_source_checkout(self):
        with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
            bootstrap.bootstrap_persistent(intent=self.intent, resources=self.resources,
                admin_url_file=self.admin_file, output_directory=self.checkout / "credentials",
                protected_checkout_root=self.checkout,
                _dependencies=(None, parse_admin, make_conninfo, self.owner))
        self.assertEqual(caught.exception.code, "private_path_inside_checkout")
        self.assertEqual(self.owner.events, [])

    def test_changed_private_outputs_cannot_reuse_valid_receipt(self):
        receipt = self.execute()
        directory = self.root / "new-runtime"
        receipt["evidence_scope"] = "observed"
        (directory / "persistent-target.json").write_text(json.dumps(receipt))
        state = json.loads((directory / "provisioning-state.json").read_text())
        state["evidence_scope"] = "observed"
        (directory / "provisioning-state.json").write_text(json.dumps(state))
        (directory / "runtime.pgpass").write_text("synthetic-replacement")
        with patch.dict(os.environ, {
            "TEMPO_LEARNING_MAINTENANCE": "0",
            "TEMPO_LEARNING_WORKSPACE": self.intent["workspace_id"],
            "TEMPO_LEARNING_ORIGINS": self.intent["ingress_origin"],
            "TEMPO_LEARNING_TIMEZONE": self.intent["timezone"],
        }):
            with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
                bootstrap.check_quarantine_receipt(directory / "persistent-target.json", bootstrap._digest(self.intent), bootstrap._digest(self.resources))
        self.assertEqual(caught.exception.code, "private_output_identity_mismatch")

    def test_complete_quarantine_protocol_guard_accepts_only_exact_bound_private_configuration(self):
        # This is an observed-PROTOCOL fixture backed by injected semantic fakes;
        # it neither observes nor qualifies a real target.
        receipt = self.execute()
        directory = self.root / "new-runtime"
        receipt["evidence_scope"] = "observed"
        (directory / "persistent-target.json").write_text(json.dumps(receipt))
        state = json.loads((directory / "provisioning-state.json").read_text())
        state["evidence_scope"] = "observed"
        (directory / "provisioning-state.json").write_text(json.dumps(state))
        connection = json.loads((directory / "connection.json").read_text())
        runtime = {
            "TEMPO_LEARNING_MAINTENANCE": "0",
            "TEMPO_LEARNING_WORKSPACE": self.intent["workspace_id"],
            "TEMPO_LEARNING_ORIGINS": self.intent["ingress_origin"],
            "TEMPO_LEARNING_TIMEZONE": self.intent["timezone"],
            "TEMPO_LEARNING_DATABASE_URL": connection["writer_dsn"],
            "TEMPO_LEARNING_READER_URL": connection["reader_dsn"],
            "PGPASSFILE": str(directory / "runtime.pgpass"),
            "TEMPO_LEARNING_TOKEN_FILE": str(directory / "study.token"),
            "TEMPO_LEARNING_OPERATOR_TOKEN_FILE": str(directory / "operator.token"),
        }
        self.owner.events.clear()
        dependencies = (None, parse_admin, make_conninfo, self.owner)
        def guard(resource=None):
            return bootstrap.check_quarantine_receipt(directory / "persistent-target.json",
                bootstrap._digest(self.intent), resource or bootstrap._digest(self.resources))
        with patch.dict(os.environ, runtime), patch.object(bootstrap, "_load_dependencies", return_value=dependencies):
            self.assertEqual(guard(), receipt)
            self.assertEqual(self.owner.events, [], "guard must not execute database or service effects")
            with self.assertRaises(bootstrap.PersistentBootstrapError):
                guard("0" * 64)
            with patch.dict(os.environ, {"TEMPO_LEARNING_DATABASE_URL": "another-explicit-database"}):
                with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
                    guard()
                self.assertEqual(caught.exception.code, "runtime_database_mismatch")
            self.owner.applied = {"different-source-schema": "b" * 64}
            with self.assertRaises(bootstrap.PersistentBootstrapError) as caught:
                guard()
            self.assertEqual(caught.exception.code, "schema_identity_mismatch")

    def test_libpq_override_refuses_before_driver_or_effects(self):
        with patch.dict(os.environ, {"PGHOSTADDR": "synthetic-untrusted-routing"}):
            with self.assertRaises(bootstrap.PersistentBootstrapError):
                self.execute()
        self.assertEqual(self.owner.events, [])


class Statement(str):
    def format(self, *values):
        return Statement(str(self).format(*values))


class Cursor:
    def __init__(self, one=None, rows=()):
        self.one, self.rows = one, rows
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.rows


class OwnerConnection:
    def __init__(self, identity=("123456789", "postgres", "admin", "admin"), exists=False, roles=()):
        self.identity, self.exists, self.roles, self.queries = identity, exists, roles, []
        self.owner = "tempo_persistent_fixture_owner"
        self.relations = []
        self.migrations = []
        self.workspace = "synthetic-fresh-workspace"
        self.populated = False
        self.role_rows = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, query, parameters=()):
        text = str(query)
        self.queries.append((text, parameters))
        if "pg_control_system()" in text:
            return Cursor(one=self.identity)
        if text.startswith("SELECT 1 FROM pg_database"):
            return Cursor(one=(1,) if self.exists else None)
        if text.startswith("SELECT rolname FROM pg_roles"):
            return Cursor(rows=self.roles)
        if "pg_get_userbyid" in text:
            return Cursor(one=(self.owner,))
        if text.startswith("SELECT n.nspname"):
            return Cursor(rows=self.relations)
        if "FROM learning.migration_history" in text:
            return Cursor(rows=self.migrations)
        if "FROM learning.instance" in text:
            return Cursor(rows=[(self.workspace,)])
        if "FROM learning.settings" in text:
            return Cursor(rows=[("America/New_York",)])
        if "FROM learning.frozen_opening_activation" in text:
            return Cursor(rows=[(1, None, 0)])
        if "FROM pg_tables" in text:
            return Cursor(rows=[("cards",)])
        if text.startswith("SELECT 1 FROM learning."):
            return Cursor(one=(1,) if self.populated else None)
        if text.startswith("SELECT rolname,rolcanlogin"):
            return Cursor(rows=self.role_rows)
        return Cursor()


def load_owner(connection):
    fake = ModuleType("psycopg")
    fake.connect = lambda *args, **kwargs: connection
    fake.sql = SimpleNamespace(SQL=Statement, Identifier=lambda v: '"' + v + '"', Literal=repr)
    remediation = ModuleType("tempo_remediation_database")
    remediation.__file__ = str(DATABASE_ROOT / "tempo_remediation_database" / "__init__.py")
    path = DATABASE_ROOT / "tempo_learning_database" / "ddl" / "migrate.py"
    specification = importlib.util.spec_from_file_location("persistent_owner_under_test", path)
    owner = importlib.util.module_from_spec(specification)
    with patch.dict(sys.modules, {"psycopg": fake, "tempo_remediation_database": remediation}):
        specification.loader.exec_module(owner)
    return owner


class PersistentOwnerBoundaryTests(unittest.TestCase):
    def test_disposable_guard_remains_before_any_connection(self):
        connection = OwnerConnection()
        owner = load_owner(connection)
        with self.assertRaises(ValueError):
            owner.provision(ADMIN, "tempo_persistent_fixture", "o", "w", "r", "x", "y")
        self.assertEqual(connection.queries, [])

    def test_persistent_identity_and_absence_rechecked_before_create(self):
        for connection in (OwnerConnection(identity=("wrong", "postgres", "admin", "admin")),
                           OwnerConnection(exists=True), OwnerConnection(roles=[("existing_role",)])):
            owner = load_owner(connection)
            name = "tempo_persistent_fixture"
            with self.assertRaises(ValueError):
                owner.provision_persistent(ADMIN, name, name + "_owner", name + "_writer", name + "_reader",
                    "w" * 40, "r" * 40, system_identifier="123456789", session_user="admin")
            self.assertFalse(any(q.startswith("CREATE ") for q, _ in connection.queries))

    def test_persistent_create_uses_empty_template_and_no_source_cleanup(self):
        connection = OwnerConnection()
        owner = load_owner(connection)
        name = "tempo_persistent_fixture"
        owner.provision_persistent(ADMIN, name, name + "_owner", name + "_writer", name + "_reader",
            "w" * 40, "r" * 40, system_identifier="123456789", session_user="admin")
        self.assertIn("pg_control_system()", connection.queries[0][0])
        self.assertTrue(any("pg_advisory_lock" in q for q, _ in connection.queries))
        self.assertTrue(any("CREATE DATABASE" in q and "TEMPLATE template0" in q for q, _ in connection.queries))
        self.assertFalse(any("DROP " in q for q, _ in connection.queries))

    def test_new_persistent_database_rejects_foreign_owner_or_nonempty_template(self):
        name = "tempo_persistent_fixture"
        for wrong_owner, populated in ((False, False), (True, False), (False, True)):
            connection = OwnerConnection(identity=("123456789", name, "admin", "admin"))
            if wrong_owner:
                connection.owner = "foreign_owner"
            if populated:
                connection.relations = [("public", "unexpected_source_table")]
            owner = load_owner(connection)
            invoke = lambda: owner.verify_new_persistent_database(ADMIN,
                expected_identity={"system_identifier": "123456789", "database": name, "session_user": "admin"},
                owner_role=name + "_owner")
            if wrong_owner or populated:
                with self.assertRaises(ValueError):
                    invoke()
            else:
                invoke()
            self.assertFalse(any(q.startswith("CREATE ") for q, _ in connection.queries))

    def test_actual_schema_and_empty_content_readback_are_required(self):
        name = "tempo_persistent_fixture"
        for corruption in (None, "checksums", "content", "workspace", "reader"):
            with self.subTest(corruption=corruption):
                connection = OwnerConnection(identity=("123456789", name, "admin", "admin"))
                owner = load_owner(connection)
                expected = owner.expected_migrations()
                connection.migrations = list(expected.items())
                connection.role_rows = [
                    (name + "_owner", False, False, False, False, False, False, True, None),
                    (name + "_writer", True, False, False, False, False, False, False, None),
                    (name + "_reader", True, False, False, False, False, False, False, ["default_transaction_read_only=on"]),
                ]
                if corruption == "checksums":
                    connection.migrations[0] = (connection.migrations[0][0], "0" * 64)
                elif corruption == "content":
                    connection.populated = True
                elif corruption == "workspace":
                    connection.workspace = "different-workspace"
                elif corruption == "reader":
                    connection.role_rows[-1] = (*connection.role_rows[-1][:-1], None)
                invoke = lambda: owner.verify_persistent_initialized(ADMIN, "synthetic-fresh-workspace",
                    "America/New_York", name + "_owner", name + "_writer", name + "_reader",
                    expected_identity={"system_identifier": "123456789", "database": name, "session_user": "admin"})
                if corruption:
                    with self.assertRaises(ValueError):
                        invoke()
                else:
                    schema = invoke()
                    self.assertEqual(schema["identity"], owner.schema_identity(expected))
                    self.assertEqual(schema["applied_migrations"], expected)

    def test_public_schema_identity_is_exact_map_not_count_or_order(self):
        owner = load_owner(OwnerConnection())
        left = {"b": "b" * 64, "a": "a" * 64}
        self.assertEqual(owner.schema_identity(left), owner.schema_identity(dict(reversed(list(left.items())))))
        self.assertNotEqual(owner.schema_identity({"a": "a" * 64}), owner.schema_identity({"b": "a" * 64}))
        for bad in ({}, {"a": "A" * 64}, {"a": 1}, {1: "a" * 64}):
            with self.assertRaises(ValueError):
                owner.schema_identity(bad)


class PersistentCompositionTests(unittest.TestCase):
    def test_quarantine_config_has_no_public_or_web_or_automatic_maintenance(self):
        path = DATABASE_ROOT.parent / "config/study-persistent/quarantine.compose.yml"
        source = path.read_text()
        self.assertNotIn("\n  study-web:", source)
        self.assertNotIn("\n    ports:", source)
        self.assertIn('TEMPO_LEARNING_MAINTENANCE: "0"', source)
        self.assertIn("check-quarantine", source)
        self.assertIn("external: true", source)
        self.assertNotIn("tempo-study-test-", source)
        self.assertNotIn("bootstrap_disposable", source)


if __name__ == "__main__":
    unittest.main()
