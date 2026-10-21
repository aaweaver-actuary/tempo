"""Fixed owning suites using Learning's existing collection/execution proof."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CHECKER = ROOT / "services/learning/application/qualification.py"


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("suite", choices=("pure", "tooling"))
    parser.add_argument("--junitxml", "--junit-xml", action="append", default=[])
    options = parser.parse_args(arguments)
    spec = importlib.util.spec_from_file_location("learning_qualification", CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    try:
        checker.junit_destination(os.environ.get("PYTEST_ADDOPTS", ""))
        if len(options.junitxml) > 1 or (options.junitxml and not options.junitxml[0]):
            raise ValueError("expected one nonempty JUnit destination")
    except ValueError as error:
        parser.error(str(error))
    paths = [HERE / "tests"] if options.suite == "pure" else [
        HERE / "integration/test_runner.py",
        ROOT / "database/tempo_remediation_database/tests/native/test_runner.py",
    ]
    with tempfile.TemporaryDirectory(prefix="tempo-remediation-suite-") as directory:
        directory = Path(directory)
        junit = Path(options.junitxml[0]).resolve() if options.junitxml else directory / "cases.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        receipts = {}
        for stage, extra in (("collection", ["--collect-only", "-q"]), ("execution", ["-q", "--junitxml=" + str(junit)])):
            receipt = directory / (stage + ".json")
            result = subprocess.run([sys.executable, str(CHECKER), str(receipt), "-c", str(HERE / "pyproject.toml"),
                "-o", "addopts=", "-p", "no:cacheprovider", *[str(path) for path in paths], *extra],
                cwd=HERE, env=checker.controlled_environment(), text=True, capture_output=True)
            print(result.stdout, end=""); print(result.stderr, end="", file=sys.stderr)
            if result.returncode: return result.returncode
            receipts[stage] = json.loads(receipt.read_text())
        identities = checker.validate_inventory(receipts["collection"], receipts["execution"], junit)
        print(json.dumps(dict(suite=options.suite, complete=True, passed=len(identities))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
