"""Explicit persistent Learning creation; never promotes disposable ownership.

Pure planning consumes an independently supplied resource observation. The CLI's
bootstrap operation creates new DB/roles only; it cannot start services, import
graphs, publish registration or admit traffic.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

INTENT_KIND = "tempo-learning-persistent-target-intent"
RESOURCE_KIND = "tempo-learning-persistent-resources"
RECEIPT_KIND = "tempo-learning-persistent-bootstrap"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_IMAGE = re.compile(r"^(sha256:[0-9a-f]{64}|[^\s@]+@sha256:[0-9a-f]{64})$")
_OUTPUTS = {"connection": "connection.json", "passfile": "runtime.pgpass",
            "study_token": "study.token", "operator_token": "operator.token"}
_PG_OVERRIDES = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
                 "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE",
                 "PGSYSCONFDIR", "PGOPTIONS", "PGTARGETSESSIONATTRS", "PGLOADBALANCEHOSTS")
_INTENT_KEYS = {"kind", "schema_version", "transition_id", "source_revision",
                "target_identity", "project", "database", "workspace_id", "timezone",
                "ingress_origin", "context", "daemon_id", "host", "port", "data_volume",
                "runtime_secrets_volume", "network_name", "source_volume_names", "images"}
_BINDING = ("context", "daemon_id", "project", "target_identity", "host", "port",
            "data_volume", "runtime_secrets_volume", "network_name")
_RESOURCE_KEYS = set(_BINDING) | {"kind", "schema_version", "evidence_scope", "evidence_id",
    "intent_sha256", "observed_at", "container_id", "network_id", "system_identifier",
    "labels", "new_data_volume", "new_runtime_secrets_volume", "postgres_image"}

class PersistentBootstrapError(Exception):
    """Safe public error code; raw driver/credential text never escapes."""
    def __init__(self, code, phase="preflight"):
        super().__init__(code)
        self.code, self.phase = code, phase

def _require(value, code):
    if not value:
        raise PersistentBootstrapError(code)

def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 for c in value)

def _json(value):
    try:
        copy = json.loads(json.dumps(value, allow_nan=False))
        _require(copy == value, "finite_json_required")
        return copy
    except (TypeError, ValueError, RecursionError):
        raise PersistentBootstrapError("finite_json_required") from None

def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def _sha(value, length=64):
    return isinstance(value, str) and bool(re.fullmatch("[0-9a-f]{" + str(length) + "}", value))

def plan_persistent_target(intent, resources):
    """Validate explicit persistent intent and observed resources, without effects.

    Resource observations belong to an external owning observer, not this policy.
    Names and labels do not establish actual ownership by themselves. Physical
    PostgreSQL identity and DB/role absence are rechecked by the DB owner API.
    """
    intent, resources = _json(intent), _json(resources)
    _require(type(intent) is dict and set(intent) == _INTENT_KEYS, "intent_fields_invalid")
    _require(intent["kind"] == INTENT_KIND and type(intent["schema_version"]) is int
             and intent["schema_version"] == 1, "persistent_intent_required")
    _require(all(_text(intent[k]) for k in _BINDING if k != "port"), "identity_missing")
    _require(_text(intent["transition_id"]) and _sha(intent["source_revision"], 40), "source_unpinned")
    database = intent["database"]
    _require(isinstance(database, str) and bool(_NAME.fullmatch(database))
             and database not in {"postgres", "template0", "template1"}
             and not database.startswith("learning_study_test_"), "persistent_database_invalid")
    _require(not intent["project"].startswith("tempo-study-test-"), "disposable_project_forbidden")
    _require(type(intent["port"]) is int and 1 <= intent["port"] <= 65535, "port_invalid")
    _require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+", intent["host"])), "host_invalid")
    _require(_text(intent["workspace_id"]) and len(intent["workspace_id"]) <= 200, "workspace_invalid")
    try:
        ZoneInfo(intent["timezone"])
        origin = urlsplit(intent["ingress_origin"])
        _require(origin.scheme in {"http", "https"} and bool(origin.hostname)
                 and not origin.username and not origin.password and not origin.path
                 and not origin.query and not origin.fragment, "ingress_invalid")
        origin.port
    except Exception:
        raise PersistentBootstrapError("timezone_or_ingress_invalid") from None
    source_volumes = intent["source_volume_names"]
    _require(type(source_volumes) is list and bool(source_volumes)
             and all(_text(v) for v in source_volumes)
             and len(set(source_volumes)) == len(source_volumes), "source_volume_identity_missing")
    volumes = {intent["data_volume"], intent["runtime_secrets_volume"]}
    _require(len(volumes) == 2 and not volumes.intersection(source_volumes), "source_volume_reuse")
    images = intent["images"]
    _require(type(images) is dict and set(images) == {"postgres", "learning", "study-web"}
             and all(isinstance(v, str) and _IMAGE.fullmatch(v) for v in images.values()), "images_unpinned")
    _require(type(resources) is dict and set(resources) == _RESOURCE_KEYS, "resource_fields_invalid")
    _require(resources["kind"] == RESOURCE_KIND and type(resources["schema_version"]) is int
             and resources["schema_version"] == 1, "persistent_resources_required")
    _require(resources["evidence_scope"] in {"observed", "synthetic-fixture"}
             and _text(resources["evidence_id"]), "resource_evidence_missing")
    _require(resources["intent_sha256"] == _digest(intent), "resource_intent_mismatch")
    _require(all(resources[k] == intent[k] for k in _BINDING), "resource_binding_mismatch")
    _require(_sha(resources["container_id"]) and _sha(resources["network_id"])
             and isinstance(resources["system_identifier"], str)
             and bool(re.fullmatch(r"[0-9]+", resources["system_identifier"])), "server_identity_missing")
    _require(resources["postgres_image"] == images["postgres"], "postgres_image_mismatch")
    _require(resources["labels"] == {"tempo.owner": intent["project"],
        "tempo.target-kind": "persistent", "tempo.disposable": "false"}, "persistent_ownership_missing")
    _require(resources["new_data_volume"] is True and resources["new_runtime_secrets_volume"] is True,
             "new_owned_volumes_required")
    try:
        observed = datetime.fromisoformat(resources["observed_at"].replace("Z", "+00:00"))
        _require(observed.utcoffset() is not None, "observation_time_invalid")
    except Exception:
        raise PersistentBootstrapError("observation_time_invalid") from None
    return {"kind": "tempo-learning-persistent-plan", "schema_version": 1,
            "intent": intent, "resources": resources, "intent_sha256": _digest(intent),
            "resource_observation_sha256": _digest(resources),
            "roles": {name: database + "_" + name for name in ("owner", "writer", "reader")}}

def _private_file(path):
    p = Path(path)
    try:
        info = p.lstat()
        _require(stat.S_ISREG(info.st_mode) and not stat.S_IMODE(info.st_mode) & 0o077,
                 "private_file_permissions")
        return p.read_text(encoding="utf-8")
    except PersistentBootstrapError:
        raise
    except Exception:
        raise PersistentBootstrapError("private_file_unavailable") from None

def _private_json(path):
    try:
        return json.loads(_private_file(path))
    except PersistentBootstrapError:
        raise
    except Exception:
        raise PersistentBootstrapError("private_json_invalid") from None

def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def _atomic_private(path, value):
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()

@contextmanager
def _new_output(path):
    directory = Path(path)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        _sync_directory(directory.parent)
    except Exception:
        raise PersistentBootstrapError("new_output_directory_required") from None
    # Exclusively created output is deliberately retained on interruption. It
    # contains private recovery state; reruns never silently reuse or erase it.
    yield directory

def _load_dependencies():
    database_source = str(Path(__file__).resolve().parents[2])
    if database_source not in sys.path:
        sys.path.insert(0, database_source)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from tempo_learning_database.ddl import migrate
    return psycopg, conninfo_to_dict, make_conninfo, migrate

def _canonical_admin(value, intent, parse, make):
    _require(not any(os.environ.get(k) for k in _PG_OVERRIDES), "libpq_override_forbidden")
    try:
        data = parse(value)
    except Exception:
        raise PersistentBootstrapError("admin_url_invalid") from None
    _require(set(data) <= {"host", "port", "dbname", "user", "password", "connect_timeout"},
             "admin_url_override_forbidden")
    _require(data.get("host") == intent["host"] and data.get("port") == str(intent["port"])
             and data.get("dbname") == "postgres" and _text(data.get("user"))
             and _text(data.get("password")), "admin_endpoint_mismatch")
    timeout = data.get("connect_timeout", "2")
    _require(isinstance(timeout, str) and timeout.isdecimal() and 1 <= int(timeout) <= 30,
             "admin_timeout_invalid")
    return make(host=intent["host"], port=str(intent["port"]), dbname="postgres",
                user=data["user"], password=data["password"], connect_timeout=timeout, options=""), data["user"]

def _escape(value):
    return str(value).replace("\\", "\\\\").replace(":", "\\:")

def bootstrap_persistent(*, intent, resources, admin_url_file, output_directory, _dependencies=None):
    """Execute only new persistent DB/schema/grants; leave all serving disabled.

    Production defaults load the owning PostgreSQL APIs. Unit fakes must use
    synthetic-fixture evidence plus explicitly injected dependencies, and every
    receipt retains that scope. No caller can turn this receipt into admission.
    """
    plan = plan_persistent_target(intent, resources)
    intent, resources, roles = plan["intent"], plan["resources"], plan["roles"]
    _require(resources["evidence_scope"] == "observed" or _dependencies is not None,
             "fixture_effects_not_connected")
    _require(not any(os.environ.get(k) for k in _PG_OVERRIDES), "libpq_override_forbidden")
    admin = _private_file(admin_url_file).strip()
    _require(bool(admin) and "\n" not in admin and "\r" not in admin, "admin_url_invalid")
    try:
        driver, parse, make, owner = _dependencies or _load_dependencies()
        admin_url, admin_user = _canonical_admin(admin, intent, parse, make)
    except PersistentBootstrapError:
        raise
    except Exception:
        raise PersistentBootstrapError("owner_dependencies_unavailable") from None
    names = (intent["database"], roles["owner"], roles["writer"], roles["reader"])
    identity = {"system_identifier": resources["system_identifier"],
                "database": intent["database"], "session_user": admin_user}
    phase = "inspect"
    with _new_output(output_directory) as directory:
        state = {"kind": "tempo-learning-persistent-bootstrap-state", "schema_version": 1,
                 "evidence_scope": resources["evidence_scope"], "intent_sha256": plan["intent_sha256"],
                 "resource_observation_sha256": plan["resource_observation_sha256"],
                 "target_identity": intent["target_identity"], "database": intent["database"],
                 "roles": roles, "phase": phase, "registration_published": False,
                 "services_started": False, "quarantine_required": True}
        def save():
            _atomic_private(directory / "provisioning-state.json",
                            (json.dumps(state, indent=2) + "\n").encode())
        save()
        try:
            owner.inspect_persistent_target(admin_url, *names,
                system_identifier=resources["system_identifier"], session_user=admin_user)
            generated = [secrets.token_urlsafe(32) for _ in range(4)]
            _require(len(set(generated)) == 4 and all(len(v) >= 32 for v in generated), "secret_generation_invalid")
            writer_password, reader_password, study_token, operator_token = generated
            writer_dsn = make(host=intent["host"], port=str(intent["port"]),
                              dbname=intent["database"], user=roles["writer"])
            reader_dsn = make(host=intent["host"], port=str(intent["port"]),
                              dbname=intent["database"], user=roles["reader"])
            connection = {"schema_version": 1, "database": intent["database"],
                "workspace_id": intent["workspace_id"], "timezone": intent["timezone"],
                "owner_role": roles["owner"], "writer_role": roles["writer"], "reader_role": roles["reader"],
                "writer_dsn": writer_dsn, "reader_dsn": reader_dsn,
                "pgpass_file": _OUTPUTS["passfile"], "study_token_file": _OUTPUTS["study_token"],
                "operator_token_file": _OUTPUTS["operator_token"]}
            passfile = "".join(":".join(_escape(v) for v in
                (intent["host"], intent["port"], intent["database"], role, password)) + "\n"
                for role, password in ((roles["writer"], writer_password), (roles["reader"], reader_password)))
            payloads = {_OUTPUTS["connection"]: json.dumps(connection, indent=2) + "\n",
                        _OUTPUTS["passfile"]: passfile, _OUTPUTS["study_token"]: study_token + "\n",
                        _OUTPUTS["operator_token"]: operator_token + "\n"}
            for name, value in payloads.items():
                _atomic_private(directory / name, value.encode())
            phase = "provision"
            state["phase"] = "provision-started"
            save()
            owner.provision_persistent(admin_url, *names, writer_password, reader_password,
                system_identifier=resources["system_identifier"], session_user=admin_user)
            state["database_created"] = True
            phase = "empty-target"
            state["phase"] = phase
            save()
            owner_url = make(admin_url, dbname=intent["database"])
            owner.verify_new_persistent_database(owner_url, expected_identity=identity, owner_role=roles["owner"])
            phase = "migrate"
            state["phase"] = phase
            save()
            owner.migrate(owner_url, intent["workspace_id"], intent["timezone"],
                          expected_identity=identity, owner_role=roles["owner"])
            state["schema_initialized"] = True
            phase = "grant-runtime"
            state["phase"] = phase
            save()
            owner.grant_runtime(owner_url, roles["writer"], roles["reader"],
                                expected_identity=identity, owner_role=roles["owner"])
            state["runtime_grants_created"] = True
            phase = "verify"
            state["phase"] = phase
            save()
            schema = owner.verify_persistent_initialized(owner_url, intent["workspace_id"], intent["timezone"],
                roles["owner"], roles["writer"], roles["reader"], expected_identity=identity)
            _require(type(schema) is dict and bool(schema.get("expected_migrations"))
                     and schema.get("applied_migrations") == schema["expected_migrations"]
                     and schema.get("migration_count") == len(schema["expected_migrations"])
                     and schema.get("identity") == owner.schema_identity(schema["expected_migrations"]),
                     "schema_readback_mismatch")
            receipt = {"kind": RECEIPT_KIND, "schema_version": 1,
                "evidence_scope": resources["evidence_scope"], "status": "initialized-quarantine-required",
                "intent_sha256": plan["intent_sha256"],
                "resource_observation_sha256": plan["resource_observation_sha256"],
                "transition_id": intent["transition_id"], "target_identity": intent["target_identity"],
                "declared_source_revision": intent["source_revision"], "declared_images": intent["images"],
                "observed_postgres_image": resources["postgres_image"],
                "project": intent["project"], "database": intent["database"], "roles": roles,
                "workspace_id": intent["workspace_id"], "timezone": intent["timezone"],
                "ingress_origin": intent["ingress_origin"], "system_identifier": resources["system_identifier"],
                "schema": schema, "private_outputs": _OUTPUTS,
                "effects": {"database_created": True, "schema_initialized": True, "runtime_grants_created": True,
                    "services_started": False, "maintenance_activated": False,
                    "public_traffic_activated": False, "registration_published": False}}
            state["phase"] = "complete-quarantine-required"
            save()
            _atomic_private(directory / "persistent-target.json", (json.dumps(receipt, indent=2) + "\n").encode())
            return receipt
        except BaseException as error:
            state["phase"] = "interrupted-or-failed"
            state["failed_during"] = phase
            state["inspection_required"] = True
            # Never remove new DB/roles, private outputs, old source or registration.
            try:
                save()
            except Exception:
                pass
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise PersistentBootstrapError("persistent_bootstrap_stopped", phase) from None

def check_quarantine_receipt(receipt_file, expected_intent_sha256):
    """Read-only startup guard; does not certify actual graph/runtime fencing."""
    receipt_path = Path(receipt_file)
    receipt = _private_json(receipt_path)
    _require(receipt.get("kind") == RECEIPT_KIND and receipt.get("schema_version") == 1
             and receipt.get("evidence_scope") == "observed"
             and receipt.get("status") == "initialized-quarantine-required"
             and receipt.get("intent_sha256") == expected_intent_sha256, "persistent_receipt_mismatch")
    state = _private_json(receipt_path.parent / "provisioning-state.json")
    _require(state.get("phase") == "complete-quarantine-required"
             and state.get("intent_sha256") == expected_intent_sha256
             and state.get("resource_observation_sha256") == receipt.get("resource_observation_sha256"),
             "persistent_completion_unconfirmed")
    effects = receipt.get("effects", {})
    _require(effects.get("services_started") is False and effects.get("maintenance_activated") is False
             and effects.get("public_traffic_activated") is False
             and effects.get("registration_published") is False, "receipt_not_quarantined")
    _require(os.environ.get("TEMPO_LEARNING_MAINTENANCE") == "0", "maintenance_must_be_disabled")
    _require(os.environ.get("TEMPO_LEARNING_WORKSPACE") == receipt["workspace_id"]
             and os.environ.get("TEMPO_LEARNING_ORIGINS") == receipt["ingress_origin"]
             and os.environ.get("TEMPO_LEARNING_TIMEZONE") == receipt["timezone"], "runtime_authority_mismatch")
    directory = receipt_path.parent
    connection = _private_json(directory / _OUTPUTS["connection"])
    _require(os.environ.get("TEMPO_LEARNING_DATABASE_URL") == connection["writer_dsn"]
             and os.environ.get("TEMPO_LEARNING_READER_URL") == connection["reader_dsn"], "runtime_database_mismatch")
    paths = {"PGPASSFILE": _OUTPUTS["passfile"], "TEMPO_LEARNING_TOKEN_FILE": _OUTPUTS["study_token"],
             "TEMPO_LEARNING_OPERATOR_TOKEN_FILE": _OUTPUTS["operator_token"]}
    for key, name in paths.items():
        path = directory / name
        _require(os.environ.get(key) == str(path), "runtime_private_path_mismatch")
        _private_file(path)
        _require(path.stat().st_uid == os.getuid(), "runtime_private_owner_mismatch")
    _require(not any(os.environ.get(k) for k in _PG_OVERRIDES if k != "PGPASSFILE"),
             "libpq_override_forbidden")
    # Reconfirm file and directory durability before opening even private health routes.
    for name in list(_OUTPUTS.values()) + ["provisioning-state.json", "persistent-target.json"]:
        descriptor = os.open(directory / name, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    _sync_directory(directory)
    return receipt

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "bootstrap"):
        command = commands.add_parser(name)
        command.add_argument("--intent-file", required=True)
        command.add_argument("--resources-file", required=True)
        if name == "bootstrap":
            command.add_argument("--admin-url-file", required=True)
            command.add_argument("--output-directory", required=True)
    check = commands.add_parser("check-quarantine")
    check.add_argument("--receipt-file", required=True)
    check.add_argument("--intent-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check-quarantine":
            check_quarantine_receipt(args.receipt_file, args.intent_sha256)
        elif args.command == "plan":
            result = plan_persistent_target(_private_json(args.intent_file), _private_json(args.resources_file))
            print(json.dumps(result, sort_keys=True))
        else:
            bootstrap_persistent(intent=_private_json(args.intent_file), resources=_private_json(args.resources_file),
                admin_url_file=args.admin_url_file, output_directory=args.output_directory)
            print("Persistent target initialized; graph import and runtime/admission verification remain required.")
        return 0
    except Exception as error:
        code = error.code if isinstance(error, PersistentBootstrapError) else "persistent_operation_failed"
        print("Persistent target refused or stopped: " + code, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
