"""Focused owning suites; separate native ownership proof, no application startup."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
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


def run(command, *, cwd=ROOT, environment=None):
    started = time.monotonic()
    subprocess.run(command, cwd=cwd, env=environment, check=True)
    print(
        f"Completed {command[0]} scope in {time.monotonic() - started:.2f}s", flush=True
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", choices=PACKAGES)
    parser.add_argument("--typescript", action="store_true")
    args = parser.parse_args()
    packages = [args.package] if args.package else PACKAGES
    for package in packages:
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(ROOT / package / "tests"),
                "--import-mode=importlib",
                "-q",
            ]
        )
    if args.package is None:
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(ROOT / "tests"),
                "--import-mode=importlib",
                "-q",
            ]
        )
    if args.typescript:
        with tempfile.TemporaryDirectory(prefix="tempo264-producers-") as directory:
            for package in PACKAGES:
                result = subprocess.run(
                    [sys.executable, str(ROOT / package / "examples/producer.py")],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                records = [json.loads(line) for line in result.stdout.splitlines()]
                if not records:
                    raise SystemExit(f"zero producer examples: {package}")
                Path(directory, package + ".json").write_text(json.dumps(records))
            run(
                ["npm", "test"],
                cwd=ROOT / "consumer-examples",
                environment={**os.environ, "TEMPO_CONTRACT_PRODUCER_OUTPUT": directory},
            )


if __name__ == "__main__":
    main()
