"""Prepared finite artifact/module-native windows. Never targets a live service.

Run --describe without resources. Actual runs require the managed development
budget entry point, an explicitly approved window and an already-local immutable
helper image containing Python. uv is one pinned, hash-verified PyPI wheel in
owned evidence under the same caps/deadline. No image is built or pulled here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile
import platform
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("contracts/content-publication", "contracts/game-evidence", "packages/chess-core",
            "services/learning/remediation", "services/game-evidence", "tools/analysis-restoration/a_bridge")
BACKEND_DEPENDENCIES = ("packages/scheduling", "services/learning/application", "services/learning/runtime")
SOURCE_PACKAGES = PACKAGES + BACKEND_DEPENDENCIES
NATIVE_NAMES = (
    "test_native_outer_rollback_includes_obligation_and_completed_receipt",
    "test_native_response_loss_restart_and_new_transport_have_one_effect",
    "test_native_attempt_frontier_cannot_be_invented_changed_or_expanded",
    "test_native_owner_projection_keeps_replay_prior_attempt_fence_and_invalidation",
    "test_native_legacy_variant_reuses_receipt_frontier_settlement_and_outer_transaction",
)
ARTIFACT_CASES = (
    ("wire-v3", "contracts/game-evidence", ("tests/test_v3.py",), 17, True),
    ("remediation", "services/learning/remediation", ("tests",), 85, True),
    ("game-evidence", "services/game-evidence", ("tests",), 125, True),
    ("bridge", "tools/analysis-restoration/a_bridge", ("tests/test_legacy.py", "tests/test_legacy_observation.py",
      "tests/test_legacy_study.py", "tests/test_operator.py::test_installed_operator_is_independent_of_all_domain_services"), 57, False),
)
WORK_SECONDS = {"artifacts": 900, "native": 600}
CLEANUP_SECONDS = 300
HELPER_MEMORY = 768 * 1024 * 1024
MODULE_SHA = "96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d"
POSTGRES_IMAGE_ID = "sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
UV_VERSION = "0.9.3"
UV_WHEELS = {
    "aarch64": ("https://files.pythonhosted.org/packages/07/19/bb8aa38b4441e03c742e71a31779f91b42d9db255ede66f80cdfdb672618/uv-0.9.3-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl", "406ab1a8b313b4b3cf67ad747fb8713a0c0cf3d3daf11942b5a4e49f60882339"),
    "x86_64": ("https://files.pythonhosted.org/packages/82/45/488417c6c0127c00bcdfac3556ae2ea0597df8245fe5f9bcfda35ebdbe85/uv-0.9.3-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "73ae4bbc7d555ba1738da08c64b55f21ab0ea0ff85636708cebaf460d98a440d"),
}


def pinned_uv(root, mode, timer, destination):
    """One reviewed binary in owned evidence, inside the helper's work budget.

    No installer script, pip, credentials, image build, image pull or fallback.
    Only artifact mode downloads; native reuses the exact verified wheel.
    """
    url, expected = UV_WHEELS[platform.machine()]
    directory = root / "tools" / ("uv-" + UV_VERSION)
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "vendor.whl"
    if not wheel.exists():
        if mode != "artifacts": raise RuntimeError("Native window requires the qualified pinned tool")
        with urllib.request.urlopen(url, timeout=20) as response:
            if response.geturl() != url: raise RuntimeError("Unreviewed tool redirect")
            data = response.read(24 * 1024 * 1024 + 1)
        if len(data) > 24 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Pinned uv wheel size/hash mismatch")
        wheel.write_bytes(data)
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Retained pinned uv wheel changed")
    with zipfile.ZipFile(wheel) as archive:
        entries = [info for info in archive.infolist() if not info.is_dir() and info.filename.rsplit('/', 1)[-1] == 'uv']
        if len(entries) != 1 or entries[0].file_size > 64 * 1024 * 1024:
            raise RuntimeError("Unexpected pinned uv wheel layout")
        binary = directory / "uv"
        binary.write_bytes(archive.read(entries[0]))
        binary.chmod(0o700)
    version = timer.capture([str(binary), "--version"], maximum=10).stdout.strip()
    if not version.startswith("uv " + UV_VERSION + " ") and version != "uv " + UV_VERSION:
        raise RuntimeError("Pinned uv version mismatch")
    write(destination / "tool.json", dict(version=version, url=url, sha256=expected))
    return str(binary)


def describe():
    return dict(work_seconds=WORK_SECONDS, cleanup_reserve_seconds=CLEANUP_SECONDS,
        helper=dict(cpus=1, memory_mib=768, swap_mib=0, pids=128, tmpfs_mib=256, docker_socket=False),
        postgres=dict(cpus=1, memory_mib=256, pids=256, tmpfs_mib=256, image=POSTGRES_IMAGE_ID),
        native_total=dict(containers=2, cpus=2, memory_mib=1024, pids=384, owned_internal_networks=1),
        own_packages=list(PACKAGES), own_artifacts=dict(wheels=6, sdists=6),
        backend_dependency_packages=list(BACKEND_DEPENDENCIES), backend_dependency_wheels=3,
        artifact_tests={name: count for name, _path, _tests, count, _mode in ARTIFACT_CASES},
        artifact_test_total=sum(item[3] for item in ARTIFACT_CASES), native_tests=list(NATIVE_NAMES),
        native_test_total=len(NATIVE_NAMES),
        pinned_tool=dict(version=UV_VERSION, source="https://pypi.org/pypi/uv/0.9.3/json",
            sha256={architecture: item[1] for architecture, item in UV_WHEELS.items()},
            maximum_download_mib=24, download_timeout_seconds=20, setup_budget="included in 900s artifact work"),
        limits="No full v1/v2 contract exchange, backend native migration/HTTP/Study or image-build qualification")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


staging = load("legacy_qualification_staging", ROOT / "scripts/legacy_qualification_staging.py")


def write(path, value):
    temporary = path.with_name(path.name + '.writing-' + uuid.uuid4().hex)
    with temporary.open('x') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def helper_arguments(image, name, evidence, mode, *, source, source_sha256, network=None, env_file=None, report_relative=""):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("An explicitly approved local immutable helper image ID is required")
    args = ["run", "--name", name, "--label", "tempo.legacy-bridge-owner=" + name,
        "--label", "tempo.disposable=true", "--cpus", "1", "--memory", "768m", "--memory-swap", "768m",
        "--pids-limit", "128", "--tmpfs", "/tmp:rw,size=256m", "--init",
        "--mount", f"type=bind,source={source},target=/source,readonly",
        "--mount", f"type=bind,source={evidence},target=/evidence",
        "--network", network or "bridge", "--env", "TEMPO_QUALIFICATION_SOURCE_SHA256=" + source_sha256,
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--env", "PYTHONPYCACHEPREFIX=/tmp/legacy-qualified-bytecode",
        "--env", "UV_CONCURRENT_BUILDS=1",
        "--env", "CARGO_BUILD_JOBS=1", "--env", "RAYON_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1"]
    if env_file: args += ["--env-file", str(env_file)]
    python = "python" if mode == "artifacts" else "/evidence/work/environment/bin/python"
    return [*args, "--entrypoint", python, image, "/source/scripts/qualify_legacy_bridge.py", "--inside", mode,
            "--report-relative", report_relative]


class Deadline:
    def __init__(self, seconds, report, *, receipt=None):
        self.end, self.report = time.monotonic() + seconds, report
        self.receipt = receipt

    def persist(self):
        if self.receipt is not None:
            write(self.receipt, self.report)

    def finish(self, entry, started, *, code=None, error=None):
        entry.update(state="failed" if error or code else "completed", exit_code=code,
            completed_at=datetime.now(timezone.utc).isoformat(),
            seconds=round(time.monotonic() - started, 3))
        if error is not None: entry["error"] = type(error).__name__
        self.report.pop("pending_stage", None)
        self.persist()

    def run(self, command, *, cwd=None, env=None, log=None, check=True, maximum=None):
        started = time.monotonic()
        entry = dict(argv=command, stage=Path(log).stem if log else Path(command[0]).name,
            state="pending", started_at=datetime.now(timezone.utc).isoformat())
        self.report.setdefault("commands", []).append(entry)
        self.report["pending_stage"] = entry["stage"]
        process = None
        try:
            # Persist before spawn; receipt I/O consumes the existing deadline.
            self.persist()
            remaining = self.end - time.monotonic() - 10
            if maximum is not None: remaining = min(remaining, maximum)
            if remaining <= 0: raise TimeoutError("Finite work/cleanup deadline reached")
            with (open(log, "w") if log else open(os.devnull, "w")) as output:
                process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                entry.update(state="running", process_group=process.pid)
                self.persist()
                remaining = min(remaining, self.end - time.monotonic() - 10)
                if remaining <= 0: raise TimeoutError("Finite work/cleanup deadline reached")
                code = process.wait(timeout=remaining)
        except BaseException as error:
            if process is not None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=5)
            self.finish(entry, started, code=getattr(process, "returncode", None), error=error)
            raise
        if code:
            # The managed CLI wrapper may fail while its Docker child survives.
            # wait() only reaps the wrapper, not that pending create/run request.
            # This session was created by us and contains only this command.
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        self.finish(entry, started, code=code)
        if check and code: raise RuntimeError("Qualification command failed; see its stage log")
        return code

    def capture(self, command, *, check=True, maximum=15):
        remaining = min(maximum, self.end - time.monotonic() - 10)
        if remaining <= 0: raise TimeoutError("Finite control-command deadline reached")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=remaining)
        except BaseException:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.communicate(timeout=5)
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check: result.check_returncode()
        return result


def verified_absence(result, identity):
    """Only an identity-specific daemon absence diagnostic is proof."""
    return (result.returncode != 0 and re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(identity) +
                r"(?![A-Za-z0-9_.-])", result.stderr) is not None
            and (re.search(r"\bno such (?:object|container|network|volume)\b", result.stderr, re.IGNORECASE) is not None
                or re.search(r"^(?:Error response from daemon:\s*)?network " + re.escape(identity) +
                    r" not found\s*$", result.stderr, re.IGNORECASE | re.MULTILINE) is not None))


def verify_helper_caps():
    quota, period = map(int, Path("/sys/fs/cgroup/cpu.max").read_text().split())
    if (quota != period or int(Path("/sys/fs/cgroup/memory.max").read_text()) != HELPER_MEMORY
            or int(Path("/sys/fs/cgroup/memory.swap.max").read_text()) != 0
            or int(Path("/sys/fs/cgroup/pids.max").read_text()) != 128):
        raise RuntimeError("Helper must have exactly the approved cgroup caps")


def run_suite(timer, python, directory, name, project, nodes, expected, importlib_mode):
    checker = load("legacy_bridge_inventory", ROOT / "services/learning/application/qualification.py")
    common = ["-c", str(project / "pyproject.toml"), "-o", "addopts=", "-p", "no:cacheprovider", "--maxfail=1"]
    if importlib_mode: common += ["--import-mode=importlib"]
    selection = [str(project / node.split("::", 1)[0]) + ("::" + node.split("::", 1)[1] if "::" in node else "")
                 for node in nodes]
    receipts = {}
    for stage, tail in (("collection", ["--collect-only", "-q"]),
                        ("execution", ["-q", "--junitxml=" + str(directory / (name + ".xml"))])):
        receipt = directory / f"{name}-{stage}.json"
        command = [python, "-I", str(ROOT / "services/learning/application/qualification.py"), str(receipt),
                   *common, *selection, *tail]
        timer.run(command, cwd=project, log=directory / f"{name}-{stage}.log")
        receipts[stage] = json.loads(receipt.read_text())
        if len(receipts[stage]["collected"]) != expected:
            raise RuntimeError("Required fixed inventory count changed; stop and rescope")
    return checker.validate_inventory(receipts["collection"], receipts["execution"], directory / (name + ".xml"))


def copy_artifact_source(source_root, restricted):
    """Use the same package boundary for private staging and artifact builds."""
    for package in SOURCE_PACKAGES:
        target = restricted / package
        target.mkdir(parents=True)
        owner_databases = {"services/learning/runtime": "tempo_learning_database", "services/learning/remediation": "tempo_remediation_database", "services/content": "tempo_content_database", "services/intelligence": "tempo_intelligence_database", "services/game-evidence": "tempo_game_evidence_database"}
        if package in owner_databases:
            name = owner_databases[package]
            shutil.copytree(source_root / "database" / name, target / "database" / name, ignore=shutil.ignore_patterns("__pycache__"))
        for item in sorted(staging.PACKAGE_PARTS):
            source = source_root / package / item
            if source.is_dir(): shutil.copytree(source, target / item, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            elif source.is_file(): shutil.copy2(source, target / item)


def inside(mode, relative):
    staging.verify_source(ROOT, os.environ.get("TEMPO_QUALIFICATION_SOURCE_SHA256"))
    verify_helper_caps()
    if sys.version_info[:2] not in {(3, 12), (3, 14)}:
        raise RuntimeError("The selected helper must use the supported qualification runtime 3.12 or 3.14")
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Report must remain under the owned evidence root")
    root = Path("/evidence")
    destination = root / relative
    destination.mkdir(parents=True, exist_ok=True)
    report = dict(complete=False, mode=mode, plan=describe(), commands=[])
    write(destination / "inside.json", report)
    timer = Deadline(WORK_SECONDS[mode], report, receipt=destination / "inside.json")
    try:
        uv = pinned_uv(root, mode, timer, destination)
        if mode == "artifacts":
            work = root / "work"
            work.mkdir()
            restricted = work / "source"
            copy_artifact_source(ROOT, restricted)
            def stage(command, label, cwd=None):
                timer.run(command, cwd=cwd, log=destination / (label + ".log"))
            environment = work / "environment"
            stage([uv, "venv", "--python", sys.executable, str(environment)], "environment")
            python = str(environment / "bin/python")
            stage([uv, "pip", "install", "--python", python, "--require-hashes", "-r",
                   str(ROOT / "contracts/requirements-test.lock")], "test-dependencies")
            for package in ("services/game-evidence", "services/learning/remediation", "tools/analysis-restoration/a_bridge"):
                stage([uv, "lock", "--check", "--project", str(restricted / package)], package.replace("/", "-") + "-lock")
            dependencies = work / "dependencies.txt"
            export = [uv, "export", "--project", str(restricted / "services/game-evidence"), "--locked",
                "--no-dev", "--no-editable", "--no-emit-project", "--format", "requirements-txt", "--output-file", str(dependencies)]
            for package in ("tempo-chess-core", "tempo-content-publication-contracts", "tempo-game-evidence-contracts"):
                export += ["--no-emit-package", package]
            stage(export, "locked-runtime-export")
            stage([uv, "pip", "install", "--python", python, "--require-hashes", "--no-deps", "-r", str(dependencies)], "runtime-dependencies")
            wheels = root / "wheels"
            wheels.mkdir()
            for package in PACKAGES:
                stage([python, "-m", "build", "--no-isolation", "--outdir", str(wheels)],
                      package.replace("/", "-") + "-build", restricted / package)
            captured = sorted(wheels.glob("*.whl"))
            if len(captured) != 6 or len(list(wheels.glob("*.tar.gz"))) != 6:
                raise RuntimeError("Expected exactly six owned wheels and six source distributions")
            backend_wheels = restricted / "backend-wheels"
            backend_wheels.mkdir()
            for package in BACKEND_DEPENDENCIES:
                stage([python, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(backend_wheels)],
                      package.replace("/", "-") + "-dependency-build", restricted / package)
            dependencies = sorted(backend_wheels.glob("*.whl"))
            if len(dependencies) != 3: raise RuntimeError("Exactly three current backend dependency wheels required")
            stage([uv, "pip", "install", "--python", python, "--no-deps", *map(str, captured + dependencies)], "install-owned-wheels")
            for package in SOURCE_PACKAGES: shutil.rmtree(restricted / package / "src")
            report["source_removed"] = True
            report["tests"] = {name: run_suite(timer, python, destination, name, restricted / package, nodes, count, mode)
                for name, package, nodes, count, mode in ARTIFACT_CASES}
            report["installed_wheels"] = staging.verify_owned_environment(root)
            report["backend_dependency_wheels"] = staging.verify_backend_environment(root)
            report["qualified_manifest_sha256"] = staging.record_qualified_inputs(root)
            report["artifacts"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in wheels.iterdir()}
        else:
            from psycopg.conninfo import conninfo_to_dict
            settings = conninfo_to_dict(os.environ["TEMPO_REMEDIATION_DATABASE"])
            if (settings.get("host"), settings.get("dbname"), settings.get("user")) != ("native-db", "learning", "learning_runtime"):
                raise RuntimeError("Only the generated owned internal fixture is accepted")
            python = "/evidence/work/environment/bin/python"
            sys.dont_write_bytecode = True
            sys.pycache_prefix = '/tmp/legacy-module-native-verified-bytecode'
            report['installed_wheels'] = staging.verify_owned_environment(root)
            nodes = tuple("integration/test_postgres.py::" + name for name in NATIVE_NAMES)
            report["tests"] = run_suite(timer, python, destination, "native", root / "work/source/services/learning/remediation",
                                         nodes, 5, True)
        report["complete"] = True
    finally:
        write(destination / "inside.json", report)


def controller(args):
    if not args.approved_window or not os.environ.get("TEMPO_DEVELOPMENT_BUDGET_TOKEN"):
        raise RuntimeError("An approved finite window and managed development ownership are required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.helper_image_id or ""):
        raise ValueError("Use a pre-approved local immutable helper image ID; no pull/build fallback")
    identity = "tempo-bridge-" + uuid.uuid4().hex
    if args.mode == "native" and (not args.artifact_run.is_dir() or not (args.artifact_run / "host.json").is_file()):
        raise ValueError("Use the exact successful recorded artifact run")
    root = args.artifact_run.resolve() if args.mode == "native" else (args.evidence.resolve() / identity)
    root.mkdir(parents=True, exist_ok=args.mode == "native")
    directory = root / ("native-" + identity) if args.mode == "native" else root
    directory.mkdir(parents=True, exist_ok=True)
    relative = str(directory.relative_to(root)) if directory != root else ""
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise RuntimeError("Qualification requires a clean pinned source head")
    report = dict(complete=False, mode=args.mode, identity=identity, image=args.helper_image_id,
        source_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        plan=describe(), commands=[], cleanup_verified=False)
    timer = Deadline(WORK_SECONDS[args.mode], report, receipt=directory / "host.json")
    def phase_expired(signum, frame): raise TimeoutError("Finite qualification phase expired")
    signal.signal(signal.SIGALRM, phase_expired)
    signal.setitimer(signal.ITIMER_REAL, WORK_SECONDS[args.mode])
    cleanup_error = None
    stage = None
    fixture = fixture_module = None
    network = None
    env_file = None
    helper = identity + "-helper"
    report["helper"] = helper
    write(directory / "host.json", report)
    try:
        stage = staging.OwnedStaging(identity, report['source_head'])
        report['staging'] = stage.record
        write(directory / 'host.json', report)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT, text=True).split('\0')
        stage.stage_source(ROOT, filter(None, tracked), SOURCE_PACKAGES)
        relative = str(directory.relative_to(root)) if directory != root else ''
        env_file = stage.output / relative / 'private-native.env'
        env_file.parent.mkdir(parents=True, exist_ok=True)
        timer.run(["docker", "image", "inspect", args.helper_image_id], log=directory / "helper-image.log", maximum=15)
        if args.mode == "native":
            prior = json.loads((root / "host.json").read_text())
            if (not prior["complete"] or not prior.get("cleanup_verified") or prior["image"] != args.helper_image_id or prior["source_head"] != report["source_head"]):
                raise RuntimeError("Native window requires the exact qualified artifact/image/head")
            artifact_report = json.loads((root / "inside.json").read_text())
            artifacts = artifact_report["artifacts"]
            if not artifact_report["complete"] or len(artifacts) != 12:
                raise RuntimeError("Artifact inventory is incomplete")
            for name, digest in artifacts.items():
                if Path(name).name != name: raise ValueError("Unsafe qualified artifact name")
                if hashlib.sha256((root / "wheels" / name).read_bytes()).hexdigest() != digest:
                    raise RuntimeError("Qualified artifact bytes changed")
            stage.stage_qualified(root, staging.qualified_inputs(root, prior), destination=stage.output)
            fixture_module = load("legacy_bridge_fixture", ROOT / "database/ownership/fixture.py")
            fixture_module.IMAGE = POSTGRES_IMAGE_ID
            def bounded_docker(*arguments, check=True):
                if arguments[0] == "run":
                    arguments = ("run", "--memory-swap", "256m", *arguments[1:])
                return timer.capture(["docker", *arguments], check=check)
            fixture_module.docker = bounded_docker
            class BoundedFixture(fixture_module.OwnershipFixture):
                def connection(self, database, role, password):
                    import psycopg
                    return psycopg.connect(host="127.0.0.1", port=self.port, dbname=database,
                        user=role, password=password, autocommit=True, connect_timeout=3,
                        options="-c statement_timeout=5000 -c lock_timeout=1000")
            fixture = BoundedFixture()
            report["fixture"] = dict(identity=fixture.identity, container=fixture.container, image_id=None)
            write(directory / "host.json", report)
            fixture.start()
            report["fixture"]["image_id"] = fixture.image_id
            limits = json.loads(timer.capture(["docker", "inspect", fixture.container, "--format", "{{json .HostConfig}}"]).stdout)
            if (limits["NanoCpus"], limits["Memory"], limits["MemorySwap"], limits["PidsLimit"]) != (1000000000, 268435456, 268435456, 256):
                raise RuntimeError("PostgreSQL fixture caps differ from its approved allocation")
            network = identity + "-network"
            report["network"] = network
            write(directory / "host.json", report)
            timer.run(["docker", "network", "create", "--internal", "--label", "tempo.legacy-bridge-owner=" + identity, network], maximum=15)
            timer.run(["docker", "network", "connect", "--alias", "native-db", network, fixture.container], maximum=15)
            wheel = next((root / "wheels").glob("tempo_learning_remediation-0.1.0-*.whl"))
            with zipfile.ZipFile(wheel) as bundle: schema = bundle.read("tempo_remediation_database/ddl/migrations/001_remediation.sql")
            if hashlib.sha256(schema).hexdigest() != MODULE_SHA: raise RuntimeError("Qualified module schema checksum changed")
            with fixture.admin("learning") as database:
                database.execute("SET statement_timeout=5000")
                database.execute("SET ROLE learning_owner")
                database.execute("SET search_path=domain,pg_catalog")
                database.execute(schema.decode())
                database.execute("CREATE TABLE remediation_test_schedule(workspace_id text,card_id text,fsrs_json text NOT NULL,history_json text NOT NULL,PRIMARY KEY(workspace_id,card_id));"
                    "CREATE TABLE remediation_test_queue(workspace_id text,card_id text,position integer,protected boolean DEFAULT false,PRIMARY KEY(workspace_id,card_id));")
            from psycopg.conninfo import make_conninfo
            dsn = make_conninfo(host="native-db", dbname="learning", user="learning_runtime", password=fixture.passwords["learning"], options="-c search_path=domain,pg_catalog")
            env_file.write_text("TEMPO_REMEDIATION_OWNED=" + fixture.identity + "\nTEMPO_REMEDIATION_DATABASE=" + dsn +
                "\nTEMPO_REMEDIATION_PLAN_DIR=/evidence/" + relative + "\n")
            env_file.chmod(0o600)
        command = ["docker", *helper_arguments(args.helper_image_id, helper, stage.output, args.mode,
            source=stage.source, source_sha256=stage.record["source_manifest_sha256"],
            network=network, env_file=env_file if args.mode == "native" else None, report_relative=relative)]
        timer.run(command, log=directory / "helper.log")
        if not json.loads((stage.output / relative / "inside.json").read_text())["complete"]:
            raise RuntimeError("Helper qualification was incomplete")
        if args.mode == 'artifacts':
            report['qualified_manifest_sha256'] = json.loads((stage.output / 'inside.json').read_text())['qualified_manifest_sha256']
        report["complete"] = True
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        # All helper descendants share its cgroup, even if a child changes its
        # process group. Stop/remove the verified helper before releasing PG.
        cleanup = Deadline(CLEANUP_SECONDS, report)
        signal.setitimer(signal.ITIMER_REAL, CLEANUP_SECONDS)
        try:
            probe = cleanup.capture(["docker", "inspect", helper, "--format", "{{json .Config.Labels}}"], check=False)
            if probe.returncode == 0:
                if json.loads(probe.stdout).get("tempo.legacy-bridge-owner") != helper: raise RuntimeError("Helper ownership mismatch")
                cleanup.run(["docker", "stop", "--time", "10", helper], check=False, maximum=20)
                cleanup.run(["docker", "rm", "--force", helper], maximum=20)
            elif verified_absence(probe, helper):
                # Reconcile the exact owned journal even if a failed startup
                # created no container. The managed adapter rechecks absence.
                cleanup.run(["docker", "rm", "--force", helper], check=False, maximum=20)
            else: raise RuntimeError("Helper absence unverified")
            if fixture is not None:
                fixture_module.docker = lambda *items, check=True: cleanup.capture(["docker", *items], check=check)
                probe = fixture_module.docker("container", "ls", "--all", "--filter", "name=^/" + fixture.container + "$", "--format", "{{.ID}}")
                if probe.stdout.strip(): fixture.started = True; fixture.close()
            if network:
                probe = cleanup.capture(["docker", "network", "inspect", network], check=False)
                if probe.returncode == 0:
                    if json.loads(probe.stdout)[0]["Labels"].get("tempo.legacy-bridge-owner") != identity: raise RuntimeError("Network ownership mismatch")
                    cleanup.run(["docker", "network", "rm", network], maximum=20)
                elif not verified_absence(probe, network): raise RuntimeError("Network absence unverified")
                probe = cleanup.capture(["docker", "network", "ls", "--filter", "label=tempo.legacy-bridge-owner=" + identity, "--format", "{{.ID}}"])
                if probe.stdout.strip(): raise RuntimeError("Owned network remains")
            probe = cleanup.capture(["docker", "container", "ls", "--all", "--filter", "label=tempo.legacy-bridge-owner=" + helper, "--format", "{{.ID}}"])
            if probe.stdout.strip(): raise RuntimeError("Owned helper remains")
            report["cleanup_verified"] = True
        except BaseException as error:
            cleanup_error = type(error).__name__ + ": " + str(error)
        if env_file is not None: env_file.unlink(missing_ok=True)
        if stage is not None:
            try:
                stage.copy_back(stage.output, root)
                stage.remove(resources_absent=report['cleanup_verified'])
            except BaseException as error:
                report['complete'] = False
                report['staging_error'] = type(error).__name__ + ': ' + str(error)
                report['staging_retained'] = str(stage.root)
        report["cleanup_error"] = cleanup_error
        report["complete"] = report["complete"] and report["cleanup_verified"]
        write(directory / "host.json", report)
        signal.setitimer(signal.ITIMER_REAL, 0)
    return 0 if report["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--inside", choices=WORK_SECONDS)
    parser.add_argument("--mode", choices=WORK_SECONDS)
    parser.add_argument("--approved-window", action="store_true")
    parser.add_argument("--helper-image-id")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--artifact-run", type=Path)
    parser.add_argument("--report-relative", default="")
    args = parser.parse_args()
    if args.describe: print(json.dumps(describe(), indent=2)); return 0
    def interrupted(signum, frame): raise KeyboardInterrupt("Finite qualification interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.inside: inside(args.inside, args.report_relative); return 0
    if not args.mode or not args.evidence or args.mode == "native" and not args.artifact_run:
        parser.error("Explicit mode/evidence and native artifact run are required")
    return controller(args)


if __name__ == "__main__":
    raise SystemExit(main())
