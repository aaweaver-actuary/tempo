"""Content-owned complete pytest inventories; never imports another service."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SERVICE = Path(__file__).resolve().parent


def controlled_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        result.pop(name, None)
    result.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    return result


def junit_destination(options, *, allow_junit=False):
    arguments = shlex.split(options)
    destination = None
    index = 0
    while index < len(arguments):
        name, separator, value = arguments[index].partition("=")
        if not allow_junit or name not in {"--junitxml", "--junit-xml"} or destination is not None:
            raise ValueError("Only one explicit JUnit destination is supported")
        if not separator:
            index += 1
            value = arguments[index] if index < len(arguments) else ""
        if not value or value.startswith("-"):
            raise ValueError("JUnit destination must be a nonempty path")
        destination = Path(value).resolve()
        index += 1
    return destination


class Inventory:
    def __init__(self):
        self.collected = []
        self.deselected = []
        self.reports = []
        self.collection_errors = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(str(report.longrepr))

    def pytest_runtest_logreport(self, report):
        self.reports.append({"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome})


def validate_inventory(collection, execution, junit):
    for receipt in (collection, execution):
        identities = receipt["collected"]
        if (receipt["exit_code"] or receipt["collection_errors"] or receipt["deselected"]
                or not identities or len(set(identities)) != len(identities)):
            raise RuntimeError("Empty, duplicate, deselected, or unsuccessful Content inventory")
    expected = set(collection["collected"])
    if set(execution["collected"]) != expected:
        raise RuntimeError("Content collection and execution identities differ")
    actual_phases = Counter((report["nodeid"], report["phase"]) for report in execution["reports"])
    expected_phases = Counter((identity, phase) for identity in expected for phase in ("setup", "call", "teardown"))
    if actual_phases != expected_phases or any(report["outcome"] != "passed" for report in execution["reports"]):
        raise RuntimeError("Required Content test setup, call, or teardown failed or skipped")
    cases = list(ET.parse(junit).iter("testcase"))
    if len(cases) != len(expected) or any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise RuntimeError("JUnit does not prove the complete passing Content inventory")
    if len({(case.get("classname"), case.get("name")) for case in cases}) != len(cases):
        raise RuntimeError("Duplicate Content JUnit identities")
    return sorted(expected)


def run_pytest(paths, evidence, *, cwd, environment=None, external_junit=None, redactions=()):
    evidence = Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    junit = Path(external_junit).resolve() if external_junit else evidence / "cases.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    environment = controlled_environment(environment)
    receipts = {}
    commands = []
    for stage, extra in (("collected", ["--collect-only", "-q"]),
                         ("executed", ["-q", "--junitxml=" + str(junit)])):
        receipt = evidence / (stage + ".json")
        command = [sys.executable, "-I", str(Path(__file__).resolve()), "_pytest", str(receipt),
                   "-o", "addopts=", "--import-mode=importlib", "-p", "no:cacheprovider",
                   *[str(path) for path in paths], *extra]
        commands.append(command)
        result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True)
        output = result.stdout + result.stderr
        for secret in redactions:
            output = output.replace(secret, "[disposable-secret-redacted]")
        (evidence / (stage + ".log")).write_text(output)
        print(output, end="")
        for path in (receipt, junit):
            if path.exists():
                text = path.read_text()
                for secret in redactions:
                    text = text.replace(secret, "[disposable-secret-redacted]")
                path.write_text(text)
        if result.returncode:
            raise RuntimeError("Content " + stage + " failed with exit " + str(result.returncode))
        receipts[stage] = json.loads(receipt.read_text())
    identities = validate_inventory(receipts["collected"], receipts["executed"], junit)
    if junit != evidence / "cases.xml":
        shutil.copy2(junit, evidence / "cases.xml")
    return {"complete": True, "passed": len(identities), "identities": identities, "commands": commands}


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if arguments and arguments[0] == "_pytest":
        import pytest
        inventory = Inventory()
        status = int(pytest.main(arguments[2:], plugins=[inventory]))
        Path(arguments[1]).write_text(json.dumps({**vars(inventory), "exit_code": status}, indent=2) + "\n")
        return status
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("suite", choices=("pure",))
    parser.add_argument("--junitxml", "--junit-xml")
    options = parser.parse_args(arguments)
    try:
        junit_destination(os.environ.get("PYTEST_ADDOPTS", ""))
        with tempfile.TemporaryDirectory(prefix="tempo-content-suite-") as directory:
            result = run_pytest([SERVICE / "tests"], directory, cwd=SERVICE,
                                external_junit=options.junitxml)
            print(json.dumps({"suite": options.suite, **result}))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
