"""Only invocation-owned PostgreSQL: no external database argument or live Compose."""

from pathlib import Path
import json, os, secrets, subprocess, sys, time, shutil, hashlib, socket

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


def main(test_paths=None, additional_environment=None, extra_sources=(), evidence_directory=None):
    if len(sys.argv) > 2:
        raise SystemExit("Only an evidence directory is accepted")
    evidence = Path(
        evidence_directory or sys.argv[1]
        if len(sys.argv) == 2
        else evidence_directory or os.environ.get(
            "TEMPO_LEARNING_EVIDENCE_DIR", str(ROOT / "test-results/learning-runtime")
        )
    ).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    write_json(
        evidence / "native.json",
        {"complete": False, "pending": "validating invocation"},
    )
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
        "process": {"pid":os.getpid(),"pgid":os.getpgid(0)},
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
            part in {".venv", "__pycache__", "dist", ".pytest_cache", "node_modules"}
            for part in path.parts
        )
    }
    report["runtime"] = sys.version
    report["dependency_inputs"] = {}
    manifest = json.loads((ROOT / "components.json").read_text())
    owners = {
        "content",
        "analysis-a-bridge",
        "learning-remediation",
        "game-evidence",
        "learning-review-application",
        "scheduling",
        "chess-core",
        "content-publication-contracts",
        "game-evidence-contracts",
        "browser-intents",
        "study-session",
    }
    for component in manifest["components"]:
        if component["id"] not in owners:
            continue
        for name in component["buildInputs"]:
            source = ROOT / component["root"] / name
            for path in sorted(source.rglob("*")) if source.is_dir() else [source]:
                if path.is_file() and not any(
                    part
                    in {
                        ".venv",
                        "node_modules",
                        "__pycache__",
                        ".pytest_cache",
                        "dist",
                        "test-results",
                    }
                    for part in path.parts
                ):
                    report["dependency_inputs"][str(path.relative_to(ROOT))] = (
                        hashlib.sha256(path.read_bytes()).hexdigest()
                    )
    for source in [
        ROOT / "app/lib",
        ROOT / "app/domain",
        ROOT / "app/const.ts",
        ROOT / "components.json",
        *extra_sources,
    ]:
        for path in sorted(source.rglob("*")) if source.is_dir() else [source]:
            if path.is_file():
                report["dependency_inputs"][str(path.relative_to(ROOT))] = (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                )
    report["evidence"] = str(evidence)
    attempted = False
    volume_attempted = False
    runtime_image_attempted = False
    try:
        image = docker(
            "image", "inspect", "postgres:18.6-trixie", "--format", "{{.Id}}"
        )
        runtime_tag = "tempo-learning:" + identity
        # Explicit file diagnostics build only their actual dependencies. The
        # regular suite and directory selections retain the image acceptance.
        require_runtime_image = not test_paths or any(path.is_dir() or path.name=='test_container.py' for path in test_paths)
        runtime_image = ''
        if require_runtime_image:
            runtime_image_attempted = True
            built = subprocess.run(
                ["docker", "build", "-f", "services/learning/runtime/Dockerfile",
                 "--label", "tempo.owner=" + identity, "-t", runtime_tag, "."],
                cwd=ROOT, capture_output=True, text=True,
            )
            (evidence / "container-build.log").write_text(built.stdout + built.stderr)
            if built.returncode:
                raise RuntimeError("Minimal runtime image build failed; see container-build.log")
            runtime_image = docker("image", "inspect", runtime_tag, "--format", "{{.Id}}")
            report["runtime_image"] = runtime_image
        report['runtime_image_required'] = require_runtime_image

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
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        fixture_port = listener.getsockname()[1]
        listener.close()
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
            f"127.0.0.1:{fixture_port}:5432",
            "--env",
            "POSTGRES_PASSWORD=" + password,
            image,
            "postgres",
            "-c",
            "max_locks_per_transaction=512",
        )
        report['observed_caps'] = json.loads(docker('container','inspect',identity,'--format',
            '{"cpu_nanos":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}}}'))
        assert report['observed_caps'] == {'cpu_nanos':1_000_000_000,'memory_bytes':512*1024*1024}
        write_json(evidence / 'native-start.json',report)
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
        readiness_started=time.monotonic()
        readiness_deadline=readiness_started+60
        while True:
            try:
                with psycopg.connect(admin) as database:
                    database.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic()>=readiness_deadline:
                    captured=subprocess.run(['docker','logs',identity],capture_output=True,text=True)
                    logs=(captured.stdout+captured.stderr).replace(password,'[disposable-secret-redacted]')
                    (evidence/'postgres-startup.log').write_text(logs)
                    raise RuntimeError("Owned PostgreSQL did not become ready; see postgres-startup.log") from None
                time.sleep(0.1)
        report['postgres_readiness_seconds']=time.monotonic()-readiness_started
        with psycopg.connect(admin) as database:
            report['postgres_lock_capacity'] = dict(database.execute(
                "SELECT name,setting FROM pg_settings WHERE name IN ('max_locks_per_transaction','max_connections')"
            ).fetchall())
        environment = {
            key: value
            for key, value in controlled_environment().items()
            if not key.startswith(
                ("TEMPO_DATABASE_", "TEMPO_LEARNING_", "PG", "CELERY_")
            )
        }
        environment.update(
            TEMPO_LEARNING_TEST_FIXTURE=identity,
            TEMPO_LEARNING_TEST_IMAGE=runtime_image,
            TEMPO_LEARNING_TEST_ADMIN=admin,
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        )
        environment.update(additional_environment or {})
        environment.pop("PYTHONPATH", None)
        # A separately installed producer is an integration-test input only. It
        # is absent from the Learning artifact and service container closure.
        producer = ROOT / "services/game-evidence"
        installed = subprocess.run(
            [
                "uv",
                "sync",
                "--project",
                str(producer),
                "--locked",
                "--no-editable",
                "--python",
                "3.12",
                "--reinstall-package",
                "tempo-game-evidence",
            ],
            env=environment,
            capture_output=True,
            text=True,
        )
        (evidence / "producer-install.log").write_text(
            installed.stdout + installed.stderr
        )
        if installed.returncode:
            raise RuntimeError("Installed Game Evidence producer unavailable")
        environment["TEMPO_LEARNING_TEST_GAME_EVIDENCE_PYTHON"] = str(
            producer / ".venv/bin/python"
        )
        for component, package, variable, executable in (
            (ROOT / 'services/learning/runtime','tempo-learning-runtime','TEMPO_LEARNING_TEST_RUNTIME_CLI','tempo-learning'),
            (ROOT / 'services/content','tempo-content','TEMPO_LEARNING_TEST_CONTENT_CLI','tempo-content'),
            (ROOT / 'tools/analysis-restoration/a_bridge','tempo-a-bridge','TEMPO_LEARNING_TEST_BRIDGE_CLI','tempo-a-bridge'),
        ):
            installed = subprocess.run(['uv','sync','--project',str(component),'--locked','--no-editable','--python','3.12','--reinstall-package',package],env=environment,capture_output=True,text=True)
            (evidence / (package+'-install.log')).write_text(installed.stdout+installed.stderr)
            if installed.returncode:
                raise RuntimeError('Installed bridge component unavailable: '+package)
            environment[variable] = str(component / '.venv/bin' / executable)
        environment['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'] = str(evidence / 'bridge')
        receipts = {}
        for stage, extra in [
            ("collected", ["--collect-only", "-q"]),
            (
                "executed",
                ["-v", "--tb=short", "--maxfail=1", "--junitxml=" + str(evidence / "native.xml")],
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
                "--basetemp=" + str(evidence / "temporary-test-data"),
                *[str(path) for path in (test_paths or [ROOT / "services/learning/runtime/tests"])],
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
            temporary = evidence / "temporary-test-data"
            if temporary.exists():
                retained = evidence / "service-logs"
                retained.mkdir(exist_ok=True)
                for source in [*temporary.rglob("server-*.log"), *temporary.rglob("study-proof-*.log")]:
                    shutil.copy2(source, retained / source.name)
                shutil.rmtree(temporary)
            report["temporary_test_data_removed"] = not temporary.exists()
        except BaseException as error:
            report["cleanup_error"] = str(error)
            report["complete"] = False
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
        try:
            if runtime_image_attempted:
                inspected = subprocess.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        runtime_tag,
                        "--format",
                        "{{json .Config.Labels}}",
                    ],
                    capture_output=True,
                    text=True,
                )
                if inspected.returncode == 0:
                    if json.loads(inspected.stdout).get("tempo.owner") != identity:
                        raise RuntimeError("Runtime image cleanup ownership mismatch")
                    docker("image", "rm", runtime_tag)
                report["runtime_image_tag_removed"] = True
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
                    if key not in ("commands", "identities", "source_inputs", "dependency_inputs")
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
