"""Attach case reports to the existing contract exchange runner without duplicating its selection."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def main():
    contracts = Path(sys.argv[1]).resolve()
    reports = Path(sys.argv[2]).resolve()
    specification = importlib.util.spec_from_file_location(
        "contract_runner", contracts / "run_tests.py"
    )
    runner = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(runner)
    original_run = runner.run
    scope = 0

    def recorded_run(command, *, cwd=contracts, environment=None):
        nonlocal scope
        scope += 1
        if command[1:3] == ["-m", "pytest"]:
            original_run(
                [*command, "--junitxml=" + str(reports / f"python-{scope}.xml")],
                cwd=cwd,
                environment=environment,
            )
        elif command == ["npm", "test"]:
            original_run(["npm", "run", "build"], cwd=cwd, environment=environment)
            node_runner = Path(__file__).with_name("test-node.mjs")
            result = subprocess.run(
                ["node", str(node_runner), str(Path(cwd) / "dist")],
                cwd=cwd,
                env=environment,
                text=True,
                capture_output=True,
            )
            print(result.stdout, end="", flush=True)
            print(result.stderr, end="", file=sys.stderr, flush=True)
            result.check_returncode()
            record = json.loads(result.stdout.strip().splitlines()[-1])
            (reports / "typescript.node.json").write_text(json.dumps(record))
        else:
            raise ValueError("Unexpected contract runner scope: " + str(command))

    runner.run = recorded_run
    sys.argv = [str(contracts / "run_tests.py"), "--typescript"]
    runner.main()


if __name__ == "__main__":
    main()
