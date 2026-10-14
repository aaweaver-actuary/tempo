"""Run only the real, owned PostgreSQL role-isolation fixture."""

from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                str(Path(__file__).resolve().parent / "tests"),
                "--import-mode=importlib",
                "-v",
                "-s",
            ]
        )
    )
