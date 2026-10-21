"""Build wheel/sdist and verify source-free Python 3.12/3.14 consumers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
from run_suite import controlled_environment, junit_destination, validate_inventory

PACKAGES = ("packages/chess-core", "contracts/content-publication", "services/content")
IGNORED = {".venv", "__pycache__", "test-results", "dist", ".pytest_cache", ".git"}


def qualify_consumers(workspace, destination, run, report, producer_python, producer_directory):
    """A separate test-only closure; Content's four consumers stay independent."""
    manifest = json.loads((ROOT / "components.json").read_text())
    components = {item["id"]: item for item in manifest["components"]}
    providers = {item["artifact"]["name"].replace("-", "_"): item
                 for item in components.values() if item.get("artifact")}
    selected = {}

    def select(component):
        if component["id"] in selected:
            return
        selected[component["id"]] = component
        for dependency in component["dependencies"]:
            if dependency in providers:
                select(providers[dependency])

    for identity in ("learning", "game-evidence"):
        select(components[identity])
    context = workspace / "consumer-build-context"
    artifacts = destination / "consumer-distributions"
    artifacts.mkdir(exist_ok=True)
    source_inputs = {}
    for component in selected.values():
        for name in component["buildInputs"]:
            source = ROOT / component["root"] / name
            target = context / component["root"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns(*IGNORED))
            else:
                shutil.copy2(source, target)
        for path in (context / component["root"]).rglob("*"):
            if path.is_file():
                source_inputs[str(path.relative_to(context))] = hashlib.sha256(path.read_bytes()).hexdigest()
        run([sys.executable, "-m", "build", "--no-isolation", "--outdir", artifacts,
             context / component["root"]], context, "consumer-build-" + component["id"])
    requirements = workspace / "consumer-requirements.txt"
    exports = []
    for identity in ("learning", "game-evidence"):
        exports.append(run(["uv", "export", "--project", context / components[identity]["root"],
                            "--locked", "--no-emit-project", "--no-emit-local", "--no-hashes", "--no-header"],
                           context, "consumer-locked-requirements-" + identity))
    requirements.write_text("\n".join(exports))
    export_script = producer_directory / "integration/test_consumers.py"
    export_script.parent.mkdir(exist_ok=True)
    shutil.copy2(SERVICE / "integration/test_consumers.py", export_script)
    publication_export = destination / "consumer-export.json"
    publication_export.write_text(run([producer_python, "-I", export_script], producer_directory, "consumer-producer-export"))
    consumer = workspace / "independent-consumers"
    consumer.mkdir()
    shutil.copy2(SERVICE / "integration/consumer_probe.py", consumer / "consumer_probe.py")
    shutil.copy2(SERVICE / "integration/test_consumers.py", consumer / "test_consumers.py")
    shutil.copy2(SERVICE / "run_suite.py", consumer / "run_suite.py")
    shutil.rmtree(context)
    run(["uv", "venv", "--python", "3.12", consumer / ".venv"], consumer, "consumer-venv")
    python = consumer / ".venv/bin/python"
    run(["uv", "pip", "install", "--python", python, "-r", requirements, *sorted(artifacts.glob("*.whl"))],
        consumer, "consumer-install")
    run([python, "-I", "-c", "import importlib.util; assert importlib.util.find_spec('tempo_content') is None"],
        consumer, "consumer-content-absent")
    receipts = {}
    junit = destination / "consumer.xml"
    for stage, extra in (("collected", ["--collect-only", "-q"]),
                         ("executed", ["-q", "--junitxml=" + str(junit)])):
        receipt = destination / ("consumer-" + stage + ".json")
        run([python, "-I", consumer / "run_suite.py", "_pytest", receipt, "--import-mode=importlib",
             "--rootdir", consumer, "-p", "no:cacheprovider", consumer / "test_consumers.py", *extra], consumer, "consumer-" + stage,
            additional_environment={"TEMPO_CONTENT_CONSUMER_EXPORT": str(publication_export)})
        receipts[stage] = json.loads(receipt.read_text())
    identities = validate_inventory(receipts["collected"], receipts["executed"], junit)
    result = json.loads(run([python, "-I", consumer / "consumer_probe.py", publication_export], consumer, "consumer-probe"))
    report["consumer_interoperability"] = {"complete": True, "python": "3.12", "result": result,
                                           "passed": len(identities), "identities": identities,
                                           "source_inputs": source_inputs,
                                           "build_sources_removed": not context.exists(),
                                           "components": sorted(selected),
                                           "artifacts": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                                         for path in artifacts.iterdir() if path.is_file()}}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("evidence", nargs="?", type=Path, default=SERVICE / "test-results/artifacts")
    parser.add_argument("--consumers", action="store_true", help="Also qualify installed Learning/Game Evidence adapters in a separate environment")
    options = parser.parse_args(arguments)
    destination = options.evidence.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    environment = {name: value for name, value in controlled_environment().items()
                   if not name.startswith(("PG", "TEMPO_", "CELERY_"))}
    environment["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR", "/private/tmp/tempo-content-108-uv-cache")
    report = {"complete": False, "commands": [], "artifacts": {}, "consumers": {}, "source_inputs": {}}
    started = time.monotonic()

    def run(arguments, cwd, label, expected=0, additional_environment=None):
        command_started = time.monotonic()
        arguments = [str(value) for value in arguments]
        result = subprocess.run(arguments, cwd=cwd, env={**environment, **(additional_environment or {})}, capture_output=True, text=True)
        (destination / (label + ".log")).write_text(result.stdout + result.stderr)
        report["commands"].append({"label": label, "argv": arguments, "cwd": str(cwd),
                                   "exit_code": result.returncode, "expected_exit": expected,
                                   "seconds": time.monotonic() - command_started})
        if result.returncode != expected:
            raise RuntimeError(label + " failed with exit " + str(result.returncode))
        return result.stdout

    try:
        junit_destination(os.environ.get("PYTEST_ADDOPTS", ""))
        report["revision"] = run(["git", "rev-parse", "HEAD"], ROOT, "revision").strip()
        report["dirty"] = run(["git", "status", "--short"], ROOT, "source-status").strip()
        for relative in (*PACKAGES, "database/tempo_content_database"):
            for path in (ROOT / relative).rglob("*"):
                if path.is_file() and not IGNORED.intersection(path.parts):
                    report["source_inputs"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="tempo-content-installed-") as temporary:
            workspace = Path(temporary)
            context = workspace / "build-context"
            for relative in PACKAGES:
                shutil.copytree(ROOT / relative, context / relative, ignore=shutil.ignore_patterns(*IGNORED))
            shutil.copytree(ROOT / "database/tempo_content_database", context / "database/tempo_content_database", ignore=shutil.ignore_patterns("__pycache__"))
            build_service = context / "services/content"
            artifacts = destination / "distributions"
            artifacts.mkdir(exist_ok=True)
            for relative in PACKAGES:
                run([sys.executable, "-m", "build", "--no-isolation", "--outdir", artifacts, context / relative],
                    context, "build-" + Path(relative).name)
            requirements = workspace / "requirements.txt"
            requirements.write_text(run(["uv", "export", "--project", build_service, "--locked", "--no-emit-project",
                                         "--no-emit-local", "--no-hashes", "--no-header"], context, "locked-requirements"))
            service_wheel = next(artifacts.glob("tempo_content-0*.whl"))
            service_sdist = next(artifacts.glob("tempo_content-0*.tar.gz"))
            for path in artifacts.iterdir():
                if path.is_file():
                    report["artifacts"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            # Retain only test inputs and the qualification harness, then remove
            # every copied production source before either consumer is created.
            test_inputs = workspace / "test-inputs"
            test_inputs.mkdir()
            shutil.copytree(build_service / "tests", test_inputs / "tests", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(build_service / "fixtures", test_inputs / "fixtures")
            shutil.copy2(build_service / "run_suite.py", test_inputs / "run_suite.py")
            shutil.rmtree(context)
            report["build_sources_removed"] = not context.exists()
            required_identities = None
            for version in ("3.12", "3.14"):
                for kind, selected in (("wheel", service_wheel), ("sdist", service_sdist)):
                    label = "python-" + version + "-" + kind
                    consumer = workspace / label
                    consumer.mkdir()
                    run(["uv", "venv", "--python", version, consumer / ".venv"], consumer, label + "-venv")
                    python = consumer / ".venv/bin/python"
                    dependencies = [path for path in artifacts.glob("*.whl") if path != service_wheel]
                    run(["uv", "pip", "install", "--python", python, "-r", requirements, *dependencies], consumer, label + "-dependencies")
                    run(["uv", "pip", "install", "--python", python, "--no-deps", "--no-build-isolation", selected], consumer, label + "-install")
                    shutil.copytree(test_inputs / "tests", consumer / "tests")
                    shutil.copytree(test_inputs / "fixtures", consumer / "fixtures")
                    shutil.copy2(test_inputs / "run_suite.py", consumer / "run_suite.py")
                    guard = ("import pathlib,sys,tempo_content; "
                             "assert 'site-packages' in pathlib.Path(tempo_content.__file__).parts; "
                             "assert not any(pathlib.Path(p).is_relative_to(" + repr(str(ROOT)) + ") for p in sys.path if p); "
                             "assert pathlib.Path(__import__('tempo_content_database').__file__).parent.joinpath('ddl/migrations').joinpath('manifest.json').is_file()")
                    run([python, "-I", "-c", guard], consumer, label + "-guard")
                    receipts = {}
                    junit = destination / (label + ".xml")
                    for stage, extra in (("collected", ["--collect-only", "-q"]),
                                         ("executed", ["-q", "--junitxml=" + str(junit)])):
                        receipt = destination / (label + "-" + stage + ".json")
                        run([python, "-I", consumer / "run_suite.py", "_pytest", receipt, "--import-mode=importlib",
                             "--rootdir", consumer, "-p", "no:cacheprovider", consumer / "tests", *extra], consumer, label + "-" + stage)
                        receipts[stage] = json.loads(receipt.read_text())
                    identities = validate_inventory(receipts["collected"], receipts["executed"], junit)
                    if required_identities is not None and identities != required_identities:
                        raise RuntimeError("Installed Content identities differ across runtime/artifact consumers")
                    required_identities = identities
                    report["consumers"][label] = {"passed": len(identities), "identities": identities}
                    run([python, "-I", consumer / "run_suite.py", "_pytest", destination / (label + "-zero.json"),
                         consumer / "tests", "-k", "no_such_content_test_108", "-q"], consumer, label + "-zero-selected", expected=5)
                    run([consumer / ".venv/bin/tempo-content", "--help"], consumer, label + "-cli")
                    run(["uv", "pip", "freeze", "--python", python], consumer, label + "-installed-dependencies")
            if options.consumers:
                producer_directory = workspace / "python-3.12-wheel"
                qualify_consumers(workspace, destination, run, report,
                                  producer_directory / ".venv/bin/python", producer_directory)
        report["temporary_consumers_removed"] = not workspace.exists()
        report["complete"] = True
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        print(report["error"], file=sys.stderr)
    finally:
        report["seconds"] = time.monotonic() - started
        (destination / "artifacts.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
