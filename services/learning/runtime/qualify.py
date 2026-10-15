"""Restricted installed consumers using the existing qualification inventory machinery."""

from pathlib import Path
import ast, hashlib, json, os, shutil, subprocess, sys, tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "application"))
from qualification import (
    controlled_environment,
    junit_destination,
    validate_inventory,
    write_json,
)

PACKAGES = (
    "services/learning/runtime",
    "services/learning/application",
    "packages/scheduling",
    "packages/chess-core",
    "contracts/content-publication",
    "contracts/game-evidence",
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Supply one evidence directory")
    junit_destination(os.environ.get("PYTEST_ADDOPTS", ""))
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    environment = controlled_environment()
    environment.pop("PYTHONPATH", None)
    report = {
        "complete": False,
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_inputs": {},
        "commands": [],
        "consumers": {},
    }
    write_json(destination / "installed.json", report)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run(arguments, cwd, expected=0):
        arguments = [str(value) for value in arguments]
        result = subprocess.run(
            arguments, cwd=cwd, env=environment, capture_output=True, text=True
        )
        index = len(report["commands"])
        (destination / f"command-{index:02d}.log").write_text(
            result.stdout + result.stderr
        )
        report["commands"].append(
            {
                "command": arguments,
                "cwd": str(cwd),
                "exit_code": result.returncode,
                "expected_exit": expected,
            }
        )
        if result.returncode != expected:
            raise RuntimeError(
                f"Command {index} failed with {result.returncode}; see command-{index:02d}.log"
            )
        return result.stdout

    try:
        manifest = json.loads((ROOT / "components.json").read_text())
        with tempfile.TemporaryDirectory(
            prefix="tempo-learning-installed-"
        ) as directory:
            context = Path(directory) / "context"
            context.mkdir()
            for relative in PACKAGES:
                component = next(
                    item for item in manifest["components"] if item["root"] == relative
                )
                for name in component["buildInputs"]:
                    source = ROOT / relative / name
                    target = context / relative / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if source.is_dir():
                        shutil.copytree(
                            source,
                            target,
                            ignore=shutil.ignore_patterns(
                                "__pycache__",
                                ".pytest_cache",
                                "node_modules",
                                ".venv",
                                "dist",
                            ),
                        )
                    else:
                        shutil.copy2(source, target)
            for path in sorted(context.rglob("*")):
                if path.is_file():
                    report["source_inputs"][str(path.relative_to(context))] = digest(
                        path
                    )
            component = next(
                item for item in manifest["components"] if item["id"] == "learning"
            )
            build = context / "services/learning/runtime"
            run(
                [
                    sys.executable,
                    ROOT / "tools/component-build/check-python.py",
                    build,
                    json.dumps(component["dependencies"]),
                    json.dumps(component["publicExports"]),
                    json.dumps(component["artifact"]),
                ],
                context,
            )
            run(
                [
                    "uv",
                    "sync",
                    "--project",
                    build,
                    "--locked",
                    "--no-editable",
                    "--python",
                    "3.14",
                ],
                context,
            )
            python = build / ".venv/bin/python"
            artifacts = destination / "artifacts"
            artifacts.mkdir(exist_ok=True)
            for relative in PACKAGES:
                run(
                    [
                        python,
                        "-m",
                        "build",
                        "--no-isolation",
                        "--outdir",
                        artifacts,
                        context / relative,
                    ],
                    context,
                )
            report["artifacts"] = {
                path.name: digest(path) for path in sorted(artifacts.iterdir())
            }
            requirements = Path(directory) / "requirements.txt"
            requirements.write_text(
                run(
                    [
                        "uv",
                        "export",
                        "--project",
                        build,
                        "--locked",
                        "--no-emit-local",
                        "--no-hashes",
                    ],
                    context,
                )
            )
            for relative in PACKAGES:
                shutil.rmtree(context / relative / "src")
            for version in ("3.12", "3.14"):
                consumer = Path(directory) / ("consumer-" + version)
                consumer.mkdir()
                run(["uv", "venv", "--python", version, consumer / ".venv"], consumer)
                python = consumer / ".venv/bin/python"
                run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        python,
                        "-r",
                        requirements,
                        *sorted(artifacts.glob("*.whl")),
                    ],
                    consumer,
                )
                shutil.copy2(
                    HERE / "tests/test_installation.py",
                    consumer / "test_installation.py",
                )
                helper = context / "services/learning/application/qualification.py"
                shutil.copy2(helper, consumer / "qualification.py")
                receipts = {}
                junit = destination / ("installed-" + version + ".xml")
                for stage, extra in (
                    ("collected", ["--collect-only", "-q"]),
                    ("executed", ["-v", "--junitxml=" + str(junit)]),
                ):
                    receipt = destination / (stage + "-" + version + ".json")
                    run(
                        [
                            python,
                            "-I",
                            consumer / "qualification.py",
                            receipt,
                            "-o",
                            "addopts=",
                            "-p",
                            "no:cacheprovider",
                            "test_installation.py",
                            *extra,
                        ],
                        consumer,
                    )
                    receipts[stage] = json.loads(receipt.read_text())
                identities = validate_inventory(
                    receipts["collected"], receipts["executed"], junit
                )
                report["consumers"][version] = {
                    "runtime": run([python, "--version"], consumer).strip(),
                    "passed": len(identities),
                    "identities": identities,
                    "dependencies": run(
                        ["uv", "pip", "freeze", "--python", python], consumer
                    ).splitlines(),
                }
                run(
                    [
                        python,
                        "-I",
                        "-m",
                        "pytest",
                        "test_installation.py",
                        "-k",
                        "missing_required_identity",
                        "-q",
                    ],
                    consumer,
                    expected=5,
                )
            report["complete"] = True
    except BaseException as error:
        report["error"] = str(error)
    finally:
        report["temporary_workspace_removed"] = not Path(
            locals().get("directory", "/unlikely-learning")
        ).exists()
        write_json(destination / "installed.json", report)
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "consumers": {
                    key: value["passed"] for key, value in report["consumers"].items()
                },
                "error": report.get("error"),
            }
        )
    )
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
