"""Fresh Content-only PostgreSQL proof; caller database URLs are never accepted."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

DATABASE = Path(__file__).resolve().parents[1]
ROOT = DATABASE.parents[1]
SERVICE = ROOT / "services/content"
if not (SERVICE / "run_suite.py").is_file():
    SERVICE = ROOT  # The independent Content sdist places run_suite.py at its root.
sys.path.insert(0, str(SERVICE))
from run_suite import controlled_environment, junit_destination, run_pytest


def docker(*arguments, check=True):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, check=check)


def verify_labels(kind, name, identity):
    template = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
    labels = json.loads(docker(kind, "inspect", name, "--format", template).stdout)
    if labels.get("tempo.owner") != identity or labels.get("tempo.disposable") != "true":
        raise RuntimeError("Content resource ownership mismatch; cleanup refused")


def resource_exists(kind, name):
    # A daemon/API error must never be reported as successful absence.
    arguments = [kind, "ls", "--filter", "name=^" + ("/" if kind == "container" else "") + name + "$",
                 "--format", "{{.Names}}" if kind == "container" else "{{.Name}}"]
    if kind == "container":
        arguments.insert(2, "--all")
    return name in docker(*arguments).stdout.splitlines()


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("evidence", nargs="?", type=Path, default=SERVICE / "test-results/native")
    parser.add_argument("--benchmark", action="store_true", help="Measure the representative fixture after the complete native suite")
    options = parser.parse_args(arguments)
    evidence = options.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    identity = "tempo-content-108-" + secrets.token_hex(8)
    volume = identity + "-data"
    report = {"owner": identity, "complete": False, "container_removed": False,
              "volume_removed": False, "external_database_accepted": False, "commands": []}
    attempted_container = attempted_volume = False
    passwords = [secrets.token_urlsafe(32) for _ in range(3)]
    administrator_password, owner_password, runtime_password = passwords
    started = time.monotonic()
    try:
        external_junit = junit_destination(os.environ.get("PYTEST_ADDOPTS", ""), allow_junit=True)
        report["revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        report["source_inputs"] = {
            str(path.relative_to(SERVICE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in (SERVICE / "src", SERVICE / "tests", SERVICE / "fixtures", SERVICE / "integration")
            for path in directory.rglob("*")
            if path.is_file() and not any(part in {"__pycache__", "test-results", ".pytest_cache"} for part in path.parts)
        }
        for name in ("pyproject.toml", "uv.lock", "run_suite.py"):
            path = SERVICE / name
            report["source_inputs"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        report["source_inputs"].update({
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in DATABASE.rglob("*")
            if path.is_file() and not any(part in {"__pycache__", "test-results", ".pytest_cache"} for part in path.parts)
        })
        report["dependency_inputs"] = {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for relative in ("packages/chess-core", "contracts/content-publication")
            for path in (ROOT / relative).rglob("*")
            if path.is_file() and not any(part in {".venv", "__pycache__", "test-results", ".pytest_cache", "dist"} for part in path.parts)
        }
        image = docker("image", "inspect", "postgres:18.6-trixie", "--format", "{{.Id}}").stdout.strip()
        report["image"] = image
        if resource_exists("container", identity) or resource_exists("volume", volume):
            raise RuntimeError("Content disposable resource identity already exists")
        attempted_volume = True
        docker("volume", "create", "--label", "tempo.owner=" + identity,
               "--label", "tempo.disposable=true", volume)
        with tempfile.TemporaryDirectory(prefix=identity + "-secret-") as secret_directory:
            secret = Path(secret_directory) / "postgres.env"
            secret.write_text("POSTGRES_PASSWORD=" + administrator_password + "\n")
            secret.chmod(0o600)
            attempted_container = True
            docker("run", "--detach", "--name", identity, "--label", "tempo.owner=" + identity,
                   "--label", "tempo.disposable=true", "--memory", "512m", "--pids-limit", "256", "--mount", "type=volume,source=" + volume + ",target=/var/lib/postgresql",
                   "--publish", "127.0.0.1::5432", "--env-file", str(secret), image,
                   "postgres", "-c", "shared_buffers=32MB", "-c", "max_connections=32")
        port = docker("inspect", identity, "--format", '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}').stdout.strip()
        admin_url = make_conninfo(host="127.0.0.1", port=port, dbname="postgres", user="postgres",
                                 password=administrator_password, connect_timeout=2)
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(admin_url) as database:
                    database.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Owned Content PostgreSQL did not become ready") from None
                time.sleep(0.1)
        with psycopg.connect(admin_url, autocommit=True) as database:
            for role, password in (("content_owner", owner_password), ("content_runtime", runtime_password)):
                database.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            database.execute("CREATE DATABASE content OWNER content_owner")
            for name in ("learning", "game_evidence", "intelligence"):
                database.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            for name in ("postgres", "template1", "content", "learning", "game_evidence", "intelligence"):
                database.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            database.execute("GRANT CONNECT ON DATABASE content TO content_runtime")
        owner_url = make_conninfo(admin_url, dbname="content", user="content_owner", password=owner_password)
        runtime_url = make_conninfo(admin_url, dbname="content", user="content_runtime", password=runtime_password)
        from tempo_content_database.ddl.migrate import migrate
        workspace_id = "workspace-content-native"
        migrate(owner_url, workspace_id, "content_runtime")
        environment = {name: value for name, value in controlled_environment().items()
                       if not name.startswith(("PG", "TEMPO_", "CELERY_"))}
        environment.update(
            TEMPO_CONTENT_DATABASE_URL=runtime_url,
            TEMPO_CONTENT_MIGRATION_DATABASE_URL=owner_url,
            TEMPO_CONTENT_WORKSPACE=workspace_id,
            TEMPO_CONTENT_RUNTIME_ROLE="content_runtime",
            TEMPO_CONTENT_TEST_ADMIN_URL=admin_url,
            TEMPO_CONTENT_TEST_CONTAINER=identity,
            TEMPO_CONTENT_TEST_VOLUME=volume,
            TEMPO_CONTENT_TEST_FOREIGN_DATABASES=json.dumps({name: make_conninfo(runtime_url, dbname=name)
                                                           for name in ("learning", "game_evidence", "intelligence")}),
        )
        result = run_pytest([DATABASE / "tests/test_postgres.py"], evidence / "cases",
                            cwd=SERVICE, environment=environment, external_junit=external_junit,
                            redactions=passwords)
        report.update(result)
        if options.benchmark:
            benchmark_path = evidence / "benchmark.json"
            command = [sys.executable, "-I", str(DATABASE / "tooling/benchmark.py"), str(benchmark_path)]
            report["commands"].append(command)
            benchmark = subprocess.run(command, cwd=SERVICE, env=environment, capture_output=True, text=True)
            output = benchmark.stdout + benchmark.stderr
            for password in passwords:
                output = output.replace(password, "[disposable-secret-redacted]")
            (evidence / "benchmark.log").write_text(output)
            if benchmark.returncode:
                raise RuntimeError("Representative Content benchmark failed with exit " + str(benchmark.returncode))
            report["benchmark"] = json.loads(benchmark_path.read_text())
        report["complete"] = True
    except Exception as error:
        report["complete"] = False
        report["error"] = type(error).__name__ + ": " + str(error)
        for password in passwords:
            report["error"] = report["error"].replace(password, "[disposable-secret-redacted]")
        print(report["error"], file=sys.stderr)
    finally:
        for kind, name, attempted, result_key in (("container", identity, attempted_container, "container_removed"),
                                                  ("volume", volume, attempted_volume, "volume_removed")):
            try:
                if attempted:
                    if resource_exists(kind, name):
                        verify_labels(kind, name, identity)
                        docker("rm", "--force", name) if kind == "container" else docker("volume", "rm", name)
                    if resource_exists(kind, name):
                        raise RuntimeError("Owned Content " + kind + " survived cleanup")
                report[result_key] = True
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(type(error).__name__ + ": " + str(error))
        report["seconds"] = time.monotonic() - started
        report["complete"] = report["complete"] and report["container_removed"] and report["volume_removed"]
        serialized = json.dumps(report, indent=2) + "\n"
        for password in passwords:
            serialized = serialized.replace(password, "[disposable-secret-redacted]")
        (evidence / "native-resource.json").write_text(serialized)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
