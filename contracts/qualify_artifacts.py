"""Build/install only allowlisted contract inputs and prove patch wire compatibility."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
PACKAGES = (
    "learning",
    "content-publication",
    "game-evidence",
    "intelligence",
    "analysis-jobs",
)
INPUTS = (
    "src",
    "schemas",
    "fixtures",
    "examples",
    "tests",
    "pyproject.toml",
    "README.md",
    "REGRESSIONS.md",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, choices=("3.12", "3.14"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "requested_python": args.python,
        "stages": [],
        "artifacts": [],
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            )
        ),
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    }
    with tempfile.TemporaryDirectory(prefix="tempo264-restricted-") as directory:
        restricted = Path(directory)
        env_path = restricted / "environment"

        def execute(command, stage, *, cwd=restricted):
            started = time.monotonic()
            result = subprocess.run(
                command, cwd=cwd, env=environment, text=True, capture_output=True
            )
            report["stages"].append(
                {
                    "stage": stage,
                    "seconds": round(time.monotonic() - started, 3),
                    "exit_code": result.returncode,
                    "output": result.stdout + result.stderr,
                }
            )
            if result.returncode:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2) + "\n")
                raise RuntimeError(stage + " failed:\n" + result.stdout + result.stderr)
            print(f"{stage}: passed ({report['stages'][-1]['seconds']}s)", flush=True)
            return result.stdout

        execute(
            ["uv", "venv", "--python", args.python, str(env_path)],
            "minimal environment",
        )
        python = str(env_path / "bin/python")
        report["actual_python"] = execute(
            [python, "--version"], "runtime identity"
        ).strip()
        execute(
            [
                "uv",
                "pip",
                "install",
                "--python",
                python,
                "--require-hashes",
                "-r",
                str(ROOT / "requirements-test.lock"),
            ],
            "locked contract test dependencies",
        )
        for name in PACKAGES:
            destination = restricted / name
            destination.mkdir()
            for item in INPUTS:
                source = ROOT / name / item
                if source.is_dir():
                    shutil.copytree(
                        source,
                        destination / item,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
                    )
                else:
                    shutil.copy2(source, destination / item)
            execute(
                [python, "-m", "build", "--no-isolation"],
                name + " wheel/sdist build",
                cwd=destination,
            )
            for artifact in sorted((destination / "dist").iterdir()):
                report["artifacts"].append(
                    {
                        "name": artifact.name,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                )
        wheels = [
            str(next((restricted / name / "dist").glob("*.whl"))) for name in PACKAGES
        ]
        execute(
            ["uv", "pip", "install", "--python", python, "--no-deps", *wheels],
            "install 1.0.0 wheels",
        )
        shutil.copy2(ROOT / "conftest.py", restricted / "conftest.py")
        shutil.copy2(ROOT / "pytest.ini", restricted / "pytest.ini")
        execute(
            [
                python,
                "-I",
                "-m",
                "pytest",
                *[str(restricted / name / "tests") for name in PACKAGES],
                "--import-mode=importlib",
                "-q",
                "--junitxml=" + str(restricted / "results.xml"),
            ],
            "installed package contract suites",
        )
        import xml.etree.ElementTree as ET

        suite = ET.parse(restricted / "results.xml").getroot()
        report["test_identities"] = [case.attrib for case in suite.iter("testcase")]
        if not report["test_identities"]:
            raise RuntimeError("zero installed-artifact tests")
        if any(
            int(group.attrib.get("skipped", 0)) for group in suite.iter("testsuite")
        ):
            raise RuntimeError("skipped installed-artifact tests")
        baseline = {}
        for name in PACKAGES:
            output = execute(
                [python, "-I", str(restricted / name / "examples/producer.py")],
                name + " baseline producer",
            )
            baseline[name] = [json.loads(line) for line in output.splitlines()]
            if not baseline[name]:
                raise RuntimeError("zero patch compatibility fixtures")
            (restricted / name / "baseline.json").write_text(json.dumps(baseline[name]))
        for name in PACKAGES:
            namespace = "tempo_" + name.replace("-", "_") + "_contracts"
            project = restricted / name / "pyproject.toml"
            project.write_text(
                project.read_text().replace('version = "1.0.0"', 'version = "1.0.1"')
            )
            init = restricted / name / "src" / namespace / "__init__.py"
            init.write_text(init.read_text().replace('"1.0.0"', '"1.0.1"'))
            execute(
                [python, "-m", "build", "--wheel", "--no-isolation"],
                name + " synthetic patch wheel",
                cwd=restricted / name,
            )
        patches = [
            str(next((restricted / name / "dist").glob("*1.0.1*.whl")))
            for name in PACKAGES
        ]
        execute(
            ["uv", "pip", "install", "--python", python, "--no-deps", *patches],
            "install synthetic 1.0.1 wheels",
        )
        for name in PACKAGES:
            patch_output = execute(
                [python, "-I", str(restricted / name / "examples/producer.py")],
                name + " patch producer",
            )
            assert [json.loads(line) for line in patch_output.splitlines()] == baseline[
                name
            ], "patch wire changed"
            namespace = "tempo_" + name.replace("-", "_") + "_contracts"
            check_script = f"""import importlib.util,json
from pathlib import Path
from {namespace} import v1
import {namespace}
assert {namespace}.__version__ == "1.0.1"
root=Path({str(restricted / name)!r})
spec=importlib.util.spec_from_file_location("consumer",root/"examples/consumer.py")
consumer=importlib.util.module_from_spec(spec);spec.loader.exec_module(consumer)
records=json.loads((root/"baseline.json").read_text())
assert records
for record in records:
    consumer.consume(record)
    expected=json.loads((root/"schemas/v1"/(record["model"]+".schema.json")).read_text())
    installed=Path({namespace}.__file__).parent/"schemas/v1"/(record["model"]+".schema.json")
    assert json.loads(installed.read_text()) == expected
print(str(len(records))+" baseline producer fixtures consumed by synthetic patch, unchanged schemas")
"""
            execute(
                [python, "-I", "-c", check_script],
                name + " 1.0.0 producer to 1.0.1 consumer",
            )
        report["compatibility"] = (
            "1.0.0 producer -> synthetic 1.0.1 consumer; patch producer output identical to baseline; wire v1 only"
        )
    report["temporary_resources_removed"] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Restricted sources and environments removed", flush=True)


if __name__ == "__main__":
    main()
