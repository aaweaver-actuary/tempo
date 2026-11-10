"""Exchange real producer output with separately maintained consumers."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = [
    "game-evidence",
    "analysis-jobs",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_python_v1_producers_exchange_with_independent_python_v1_consumers(package):
    output = subprocess.run(
        [sys.executable, str(ROOT / package / "examples/producer.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    records = [json.loads(line) for line in output.stdout.splitlines()]
    assert records, "zero producer examples"
    specification = importlib.util.spec_from_file_location(
        "consumer_" + package.replace("-", "_"), ROOT / package / "examples/consumer.py"
    )
    consumer = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(consumer)
    decisions = {record["case"]: consumer.consume(record) for record in records}
    if package == "game-evidence":
        assert decisions["delivery"] == "submit_obligations_without_queue_write"
        assert decisions["ack-accepted_pending"] == "keep_delivery_pending_or_failed"
        assert decisions["ack-completed"] == "acknowledge_completion"
    else:
        assert decisions["cancel-requested"] == "termination_unconfirmed"
        assert decisions["cancel-terminated"] == "computation_stopped"
