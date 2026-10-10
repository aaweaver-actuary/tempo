"""Collect or read pytest identities; collection never constitutes execution."""
import json
import sys
import xml.etree.ElementTree as xml
from pathlib import Path


def junit_results(path):
    root = xml.parse(path).getroot()
    return [{"id": item.attrib["classname"] + "::" + item.attrib["name"],
             "status": "failed" if item.find("failure") is not None or item.find("error") is not None
             else "skipped" if item.find("skipped") is not None else "passed", "retries": 0}
            for item in root.iter("testcase")]


def collect(arguments, output):
    import pytest

    class Inventory:
        def pytest_collection_finish(self, session):
            records = []
            for item in session.items:
                address, bracket, parameters = item.nodeid.partition("[")
                components = address.split("::")
                components[-1] += bracket + parameters
                module = components[0].removesuffix(".py").replace("/", ".")
                records.append({"id": ".".join([module, *components[1:-1]]) + "::" + components[-1],
                                "file": components[0]})
            Path(output).write_text(json.dumps(records))

    return pytest.main([*arguments, "--collect-only"], plugins=[Inventory()])


if __name__ == "__main__":
    if sys.argv[1] == "--results":
        print(json.dumps(junit_results(sys.argv[2])))
    else:
        sys.exit(collect(sys.argv[2:], sys.argv[1]))
