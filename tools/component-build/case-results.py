"""Read actual pytest JUnit identities and the existing structured Node runner reports."""

import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def read_results(directory):
    result = {
        "executedIdentities": [],
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "todo": 0,
    }
    for report in sorted(Path(directory).glob("*.xml")):
        for ordinal, case in enumerate(ET.parse(report).getroot().iter("testcase")):
            if case.find("skipped") is not None:
                result["skipped"] += 1
            elif case.find("failure") is not None or case.find("error") is not None:
                result["failed"] += 1
            else:
                result["passed"] += 1
                result["executedIdentities"].append(
                    f"{case.get('classname')}::{case.get('name')} [{report.name}:{ordinal + 1}]"
                )
    for report in sorted(Path(directory).glob("*.node.json")):
        node = json.loads(report.read_text())
        if not node.get("success") or node.get("failed") or not node.get("passed"):
            raise ValueError("Invalid Node suite result")
        for field in ("passed", "failed", "skipped", "todo"):
            result[field] += node[field]
        result["executedIdentities"].extend(node["executedIdentities"])
    if not result["passed"] or result["failed"] or result["skipped"]:
        raise ValueError(
            "Failed, skipped, missing or zero executed test cases: "
            + json.dumps(result)
        )
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(read_results(sys.argv[1])))
    except (OSError, ValueError, ET.ParseError) as error:
        raise SystemExit(str(error))
