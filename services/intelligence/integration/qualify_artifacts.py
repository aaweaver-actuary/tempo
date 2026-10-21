"""Build declared artifacts, remove sources, then prove clean installed consumers."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
IGNORED = {".venv", "__pycache__", ".pytest_cache", "test-results", "dist", "node_modules", ".git"}


def successful_cases(path):
    cases = ET.parse(path).getroot().findall(".//testcase")
    if not cases or any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise RuntimeError("missing_or_nonpassing_installed_cases")
    return [{"classname": case.get("classname"), "name": case.get("name")} for case in cases]


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    package_only=bool(arguments and arguments[0]=="--package-only")
    if package_only:arguments=arguments[1:]
    if len(arguments) > 1:
        raise SystemExit("only_evidence_directory_is_supported")
    destination = Path(arguments[0]).resolve() if arguments else SERVICE / "test-results/artifacts"
    destination.mkdir(parents=True, exist_ok=True)
    report = {"mode":"package-only" if package_only else "package-and-native", "native_status":"not_selected" if package_only else "required", "complete": False, "commands": [], "artifacts": {}, "cases": {}, "native": {}, "source_inputs": {}}
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PG", "TEMPO_", "PYTHON", "PYTEST_", "VIRTUAL_ENV", "CELERY_"))
    }
    environment.update(
        UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "tempo-intelligence-109-uv-cache")),
        UV_PYTHON_DOWNLOADS="never", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
    )

    def run(command, cwd, label, *, extra_environment=None):
        command = [str(value) for value in command]
        report["commands"].append({"label": label, "argv": command, "cwd": str(cwd)})
        process = subprocess.run(command, cwd=cwd, env={**environment, **(extra_environment or {})}, capture_output=True, text=True)
        (destination / (label + ".log")).write_text(process.stdout + process.stderr)
        if process.returncode:
            raise RuntimeError(label + "_failed")
        return process.stdout

    try:
        report["revision"] = run(["git", "rev-parse", "HEAD"], ROOT, "revision").strip()
        report["source_status"] = run(["git", "status", "--short"], ROOT, "source-status").strip()
        sources = [
            ("chess-core", ROOT / "packages/chess-core"),
            ("analysis-contract", ROOT / "contracts/analysis-jobs"),
            ("intelligence", SERVICE),
        ]
        for _, source in sources:
            for path in sorted(source.rglob("*")):
                if path.is_file() and not set(path.parts) & IGNORED:
                    report["source_inputs"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "database/tempo_intelligence_database").rglob("*")):
            if path.is_file() and not set(path.parts) & IGNORED:
                report["source_inputs"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="tempo-intelligence-artifacts-") as temporary:
            workspace = Path(temporary).resolve()
            distributions = destination / "distributions"
            distributions.mkdir(exist_ok=True)
            for label, source in sources:
                copied_source = workspace / (label + "-build")
                shutil.copytree(source, copied_source, ignore=shutil.ignore_patterns(*IGNORED))
                if source == SERVICE:
                    shutil.copytree(ROOT / "database/tempo_intelligence_database", copied_source / "database/tempo_intelligence_database", ignore=shutil.ignore_patterns("__pycache__"))
                run([sys.executable, "-m", "build", "--no-isolation", "--outdir", distributions], copied_source, label + "-build")
            requirements = workspace / "requirements.txt"
            requirements.write_text(run(["uv", "export", "--locked", "--no-emit-project", "--no-emit-local", "--no-hashes", "--no-header"], SERVICE, "locked-requirements"))
            service_wheel = distributions / "tempo_intelligence-0.1.0-py3-none-any.whl"
            service_sdist = distributions / "tempo_intelligence-0.1.0.tar.gz"
            dependency_wheels = [
                distributions / "tempo_chess_core-0.1.0-py3-none-any.whl",
                distributions / "tempo_analysis_jobs_contracts-1.0.0-py3-none-any.whl",
            ]
            for artifact in [service_wheel, service_sdist, *dependency_wheels]:
                report["artifacts"][artifact.name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            for copied_source in workspace.glob("*-build"):
                shutil.rmtree(copied_source)
            report["build_sources_removed"] = not any(workspace.glob("*-build"))
            if not report["build_sources_removed"]:
                raise RuntimeError("copied_build_sources_remain")
            for version in ("3.12", "3.14"):
                for kind, artifact in (("wheel", service_wheel), ("sdist", service_sdist)):
                    label = "python-" + version + "-" + kind
                    consumer = workspace / label
                    consumer.mkdir()
                    run(["uv", "venv", "--python", version, consumer / ".venv"], workspace, label + "-venv")
                    python = consumer / ".venv/bin/python"
                    run(["uv", "pip", "install", "--python", python, "-r", requirements, *dependency_wheels], consumer, label + "-dependencies")
                    run(["uv", "pip", "install", "--python", python, "--no-build-isolation", artifact], consumer, label + "-install")
                    for directory in ("tests", "fixtures", "integration"):
                        shutil.copytree(SERVICE / directory, consumer / directory, ignore=shutil.ignore_patterns("__pycache__", "qualify_artifacts.py"))
                    for directory in ("tests", "tooling"):
                        shutil.copytree(ROOT / "database/tempo_intelligence_database" / directory,
                            consumer / "database/tempo_intelligence_database" / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
                    shutil.copy2(SERVICE / "pyproject.toml", consumer / "pyproject.toml")
                    guard = r'''
from importlib import metadata
from pathlib import Path
import sys
import tempo_intelligence as intelligence
package=Path(intelligence.__file__).parent
assert 'site-packages' in package.parts
assert metadata.version('tempo-intelligence') == '0.1.0'
assert (pathlib.Path(__import__('tempo_intelligence_database').__file__).parent/'ddl/migrations/manifest.json').is_file()
assert (pathlib.Path(__import__('tempo_intelligence_database').__file__).parent/'ddl/migrations/0001_sources.sql').is_file()
assert not (Path.cwd()/'src').exists()
assert not any('backend' in path for path in sys.path)
for name in ('tempo-learning-runtime','tempo-game-evidence','tempo-content','tempo-stockfish-client','celery','redis','torch','onnxruntime','transformers'):
    try: metadata.distribution(name)
    except metadata.PackageNotFoundError: pass
    else: raise AssertionError('Unrelated runtime distribution: '+name)
'''
                    run([python, "-I", "-c", guard], consumer, label + "-installed-guard")
                    junit = destination / (label + ".xml")
                    run([python, "-I", "-m", "pytest", "tests", "--import-mode=importlib", "-q", "--junitxml=" + str(junit)], consumer, label + "-tests")
                    report["cases"][label] = successful_cases(junit)
                    run([consumer / ".venv/bin/tempo-intelligence", "--help"], consumer, label + "-cli")
                    if not package_only and (version, kind) in {("3.12", "wheel"), ("3.14", "sdist")}:
                        native_destination = destination / (label + "-native")
                        run([python, "database/tempo_intelligence_database/tooling/run_postgres.py", native_destination], consumer, label + "-native", extra_environment={"TEMPO_INTELLIGENCE_TEST_REVISION": report["revision"]})
                        native_report = json.loads((native_destination / "native-resource.json").read_text())
                        if not native_report.get("complete"):
                            raise RuntimeError("installed_native_proof_incomplete")
                        report["native"][label] = native_report
        report["complete"] = True
    except Exception as error:
        # Fixed failure labels avoid copying secrets from child exceptions.
        report["error"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("Artifact qualification failed: " + report["error"], file=sys.stderr)
    finally:
        (destination / "artifacts.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
