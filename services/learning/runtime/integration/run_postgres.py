"""Only invocation-owned PostgreSQL: no external database argument or live Compose."""

from pathlib import Path
import json, os, secrets, subprocess, sys, time, shutil, hashlib

ROOT = Path(__file__).resolve().parents[4]
APPLICATION = ROOT / "services/learning/application"
sys.path.insert(0, str(APPLICATION))
from qualification import (
    controlled_environment,
    junit_destination,
    validate_inventory,
    write_json,
)


def docker(*arguments):
    return subprocess.run(
        ["docker", *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def main():
    if len(sys.argv) > 2:
        raise SystemExit("Only an evidence directory is accepted")
    evidence = Path(
        sys.argv[1]
        if len(sys.argv) == 2
        else "/Users/andy/.codex/worktrees/learning-runtime-recovery/tempo-evidence"
    ).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    external_junit = junit_destination(
        os.environ.get("PYTEST_ADDOPTS", ""), allow_junit=True
    )
    evidence_root = evidence
    evidence = evidence_root / ("run-" + secrets.token_hex(8))
    evidence.mkdir()
    write_json(
        evidence_root / "native.json", {"complete": False, "pending": str(evidence)}
    )
    identity = "tempo-learning-" + secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    report = {
        "identity": identity,
        "complete": False,
        "container_absent": None,
        "commands": [],
    }
    report["revision"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    report["source_inputs"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "services/learning/runtime").rglob("*"))
        if path.is_file()
        and not any(
            part in {".venv", "__pycache__", "dist", ".pytest_cache"}
            for part in path.parts
        )
    }
    report["runtime"] = sys.version
    report["evidence"] = str(evidence)
    attempted = False
    volume_attempted = False
    try:
        image = docker(
            "image", "inspect", "postgres:18.6-trixie", "--format", "{{.Id}}"
        )
        report["image"] = image
        volume_attempted = True
        docker(
            "volume",
            "create",
            "--label",
            "tempo.owner=" + identity,
            "--label",
            "tempo.disposable=true",
            identity + "-data",
        )
        attempted = True
        docker(
            "run",
            "--detach",
            "--name",
            identity,
            "--label",
            "tempo.owner=" + identity,
            "--label",
            "tempo.disposable=true",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--mount",
            "type=volume,source=" + identity + "-data,target=/var/lib/postgresql",
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_PASSWORD=" + password,
            image,
        )
        port = docker(
            "inspect",
            identity,
            "--format",
            '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
        )
        import psycopg
        from psycopg.conninfo import make_conninfo

        admin = make_conninfo(
            host="127.0.0.1",
            port=port,
            dbname="postgres",
            user="postgres",
            password=password,
            connect_timeout=2,
        )
        for attempt in range(100):
            try:
                with psycopg.connect(admin) as database:
                    database.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Owned PostgreSQL did not become ready")
        environment = {
            key: value
            for key, value in controlled_environment().items()
            if not key.startswith(
                ("TEMPO_DATABASE_", "TEMPO_LEARNING_", "PG", "CELERY_")
            )
        }
        environment.update(
            TEMPO_LEARNING_TEST_FIXTURE=identity,
            TEMPO_LEARNING_TEST_ADMIN=admin,
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        )
        receipts = {}
        for stage, extra in [
            ("collected", ["--collect-only", "-q"]),
            (
                "executed",
                ["-v", "--tb=line", "--junitxml=" + str(evidence / "native.xml")],
            ),
        ]:
            receipt = evidence / (stage + ".json")
            command = [
                sys.executable,
                str(APPLICATION / "qualification.py"),
                str(receipt),
                "-c",
                str(ROOT / "services/learning/runtime/pyproject.toml"),
                "-o",
                "addopts=",
                "-p",
                "no:cacheprovider",
                str(ROOT / "services/learning/runtime/tests"),
                *extra,
            ]
            report["commands"].append(command)
            result = subprocess.run(
                command, env=environment, cwd=ROOT, capture_output=True, text=True
            )
            (evidence / (stage + ".log")).write_text(result.stdout + result.stderr)
            for output_line in result.stdout.splitlines():
                if (
                    output_line.startswith(
                        ("services/learning/runtime/tests/", "tests/")
                    )
                    or (" passed" in output_line and output_line.startswith("="))
                    or output_line.startswith("E   ")
                ):
                    print(output_line[:700])
            if result.stderr:
                print(result.stderr[-1000:], file=sys.stderr)
            if result.returncode:
                raise RuntimeError(
                    "Runtime " + stage + " failed: " + str(result.returncode)
                )
            receipts[stage] = json.loads(receipt.read_text())
        report["identities"] = validate_inventory(
            receipts["collected"], receipts["executed"], evidence / "native.xml"
        )
        report["passed"] = len(report["identities"])
        if external_junit:
            external_junit.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(evidence / "native.xml", external_junit)
        report["complete"] = True
    except BaseException as error:
        report["error"] = str(error)
    finally:
        try:
            if attempted:
                matches = docker(
                    "container",
                    "ls",
                    "--all",
                    "--filter",
                    "name=^/" + identity + "$",
                    "--format",
                    "{{.ID}}",
                ).split()
                for container in matches:
                    labels = json.loads(
                        docker(
                            "inspect", container, "--format", "{{json .Config.Labels}}"
                        )
                    )
                    if (
                        labels.get("tempo.owner") != identity
                        or labels.get("tempo.disposable") != "true"
                    ):
                        raise RuntimeError("Cleanup ownership mismatch")
                    docker("rm", "--force", container)
                if docker(
                    "container",
                    "ls",
                    "--all",
                    "--filter",
                    "name=^/" + identity + "$",
                    "--format",
                    "{{.ID}}",
                ):
                    raise RuntimeError("Owned container survived cleanup")
                report["container_absent"] = True
        except BaseException as error:
            report["cleanup_error"] = str(error)
            report["complete"] = False
        try:
            if volume_attempted:
                volumes = docker(
                    "volume",
                    "ls",
                    "--filter",
                    "name=^" + identity + "-data$",
                    "--format",
                    "{{.Name}}",
                ).split()
                for volume in volumes:
                    labels = json.loads(
                        docker(
                            "volume", "inspect", volume, "--format", "{{json .Labels}}"
                        )
                    )
                    if (
                        labels.get("tempo.owner") != identity
                        or labels.get("tempo.disposable") != "true"
                    ):
                        raise RuntimeError("Volume cleanup ownership mismatch")
                    docker("volume", "rm", volume)
                report["volume_absent"] = not docker(
                    "volume",
                    "ls",
                    "--filter",
                    "name=^" + identity + "-data$",
                    "--format",
                    "{{.Name}}",
                )
        except BaseException as error:
            report["cleanup_error"] = str(error)
            report["complete"] = False
        write_json(evidence / "native.json", report)
        write_json(evidence_root / "native.json", report)
    if not report["complete"]:
        print(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in ("commands", "identities")
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
