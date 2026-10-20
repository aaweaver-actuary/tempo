"""Stdlib-only producer for the native host-reader idle/stall regression."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "services/learning/runtime/src/tempo_learning_runtime/host_activity.py"
specification = importlib.util.spec_from_file_location("tested_host_activity", source)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
now = 100_000_000_000
activity = module.HostActivity(Path(sys.argv[1]), source="learning-idle-stall", clock=lambda: now)
activity._publish()
path = activity.directory / (activity.producer + ".json")
idle = path.read_bytes()
entered, release = threading.Event(), threading.Event()
original_replace = module.os.replace


def stalled_replace(temporary, destination):
    entered.set()
    assert release.wait(5), "parent must release its own optional publisher"
    return original_replace(temporary, destination)


try:
    with patch.object(module.os, "replace", stalled_replace):
        activity.start()
        assert entered.wait(2), "publisher did not reach injected stall"
        # This essential work must finish while the file publisher is blocked.
        with activity.request():
            assert activity.requests == 1
        assert activity.requests == 0
        assert path.read_bytes() == idle
        print(json.dumps({"now": str(now), "producer": activity.producer,
                          "essentialCompleted": True, "publisherBlocked": not release.is_set(),
                          "sourcePath": str(source), "sourceSHA256": hashlib.sha256(source.read_bytes()).hexdigest()}), flush=True)
        assert sys.stdin.readline().strip() == "close", "parent must request task-owned cleanup"
        activity.close()
        assert not release.is_set()
        release.set()
        activity.thread.join(timeout=2)
        assert not activity.thread.is_alive()
        assert not path.exists()
finally:
    activity.close()
    release.set()
    if activity.thread:
        activity.thread.join(timeout=2)
