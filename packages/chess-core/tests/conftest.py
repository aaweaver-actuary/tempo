import json


def pytest_collection_finish(session):
    print(json.dumps({"collectedIdentities": [item.nodeid for item in session.items]}))


def pytest_terminal_summary(terminalreporter):
    passed = terminalreporter.stats.get("passed", [])
    print(json.dumps({"executedIdentities": [report.nodeid for report in passed], "passed": len(passed)}))
