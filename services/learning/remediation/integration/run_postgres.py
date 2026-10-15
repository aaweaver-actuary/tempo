"""Reuse the domain fixture and Learning's existing exact-inventory checks."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    qualification = load("learning_qualification", ROOT / "services/learning/application/qualification.py")
    fixture_module = load("ownership_fixture", ROOT / "contracts/database-ownership/fixture.py")
    if len(sys.argv) > 2:
        raise ValueError("expected only an evidence directory; external databases are unsupported")
    external_junit = qualification.junit_destination(os.environ.get("PYTEST_ADDOPTS", ""), allow_junit=True)
    evidence = Path(sys.argv[1] if len(sys.argv) == 2 else os.environ.get(
        "TEMPO_REMEDIATION_EVIDENCE_DIR", ROOT / "test-results/learning-remediation")).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    run = evidence / "runs" / uuid.uuid4().hex
    run.mkdir(parents=True)
    fixture = fixture_module.OwnershipFixture()
    report = dict(complete=False, exit_code=1, fixture=fixture.identity, image=None, cleanup_verified=False,
                  error=None, cleanup_error=None, commands=[], run=str(run), runtime=sys.version,
                  revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
    qualification.write_json(evidence / "native.json", report)
    creation_attempted = False
    try:
        creation_attempted = True
        fixture.start()
        report["image"] = fixture.image_id
        from psycopg.conninfo import make_conninfo
        with fixture.admin("learning") as administration:
            administration.execute("SET ROLE learning_owner")
            administration.execute("SET search_path=domain,pg_catalog")
            administration.execute((HERE.parent / "migrations/001_remediation.sql").read_text())
            administration.execute("""CREATE TABLE remediation_test_schedule(workspace_id text,card_id text,
                fsrs_json text NOT NULL,history_json text NOT NULL,PRIMARY KEY(workspace_id,card_id));
                CREATE TABLE remediation_test_queue(workspace_id text,card_id text,position integer,
                protected boolean DEFAULT false,PRIMARY KEY(workspace_id,card_id));""")
        environment = qualification.controlled_environment()
        environment.update(TEMPO_REMEDIATION_OWNED=fixture.identity,
            TEMPO_REMEDIATION_DATABASE=make_conninfo(host="127.0.0.1", port=fixture.port, dbname="learning",
                user="learning_runtime", password=fixture.passwords["learning"], options="-c search_path=domain,pg_catalog"))
        environment.pop("PYTEST_ADDOPTS", None)
        receipts = {}
        junit = external_junit or run / "native.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        for stage, extra in (("collected", ["--collect-only", "-q"]), ("executed", ["-q", "--junitxml=" + str(junit)])):
            receipt = run / (stage + ".json")
            command = [sys.executable, str(ROOT / "services/learning/application/qualification.py"), str(receipt),
                "-c", str(HERE.parent / "pyproject.toml"), "-o", "addopts=", "-p", "no:cacheprovider",
                str(HERE / "test_postgres.py"), *extra]
            result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
            (run / (stage + ".log")).write_text(result.stdout + result.stderr)
            print(result.stdout, end=""); print(result.stderr, end="", file=sys.stderr)
            report["commands"].append(command)
            report[stage + "_exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError(stage + " failed; see invocation log")
            receipts[stage] = json.loads(receipt.read_text())
        report["identities"] = qualification.validate_inventory(receipts["collected"], receipts["executed"], junit)
        report["passed"] = len(report["identities"])
        if junit != run / "native.xml":
            shutil.copy2(junit, run / "native.xml")
        report["exit_code"] = 0
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        try:
            if creation_attempted:
                # start() may have created a container before raising.
                containers = fixture_module.docker("container", "ls", "--all", "--filter",
                    "name=^/" + fixture.container + "$", "--format", "{{.ID}}").stdout.split()
                if containers:
                    fixture.started = True
                    fixture.close()  # Existing owner/disposable checks and absence proof.
            report["cleanup_verified"] = True
        except BaseException as error:
            report["cleanup_error"] = type(error).__name__ + ": " + str(error)
            report["exit_code"] = 1
        report["complete"] = report["exit_code"] == 0 and report["cleanup_verified"]
        qualification.write_json(run / "native.json", report)
        qualification.write_json(evidence / "native.json", report)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
