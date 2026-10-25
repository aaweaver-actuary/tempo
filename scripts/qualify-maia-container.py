#!/usr/bin/env python3
"""Run Maia's existing qualification in one bounded, disposable Linux container."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Task-owned disposable checkout; Linux dependencies are written here")
    parser.add_argument("--evidence", type=Path, required=True, help="New persistent evidence directory outside the checkout")
    options = parser.parse_args()
    source, evidence = options.source.resolve(), options.evidence.resolve()
    if evidence == source or source in evidence.parents or evidence in source.parents:
        parser.error("Source and evidence must be separate, non-nested task directories")
    evidence.mkdir(parents=True, exist_ok=False)
    component = source / "workers/maia"
    if (source / "node_modules").exists() or (component / "node_modules").exists():
        parser.error("Use a fresh checkout without host node_modules")
    metadata = json.loads((component / "package.json").read_text())
    version = metadata["engines"]["node"]
    image = re.search(r"^FROM (\S+)", (source / "Dockerfile.maia").read_text(), re.M).group(1)
    if not re.fullmatch(r"node:" + re.escape(version) + r"-bookworm-slim@sha256:[0-9a-f]{64}", image):
        parser.error("Dockerfile.maia must pin the exact declared Node version and official node digest")
    owner = "maia-qualification-" + uuid4().hex
    name = "tempo-" + owner
    report = {"complete": False, "owner": owner, "container": name, "image_reference": image,
              "started_utc": datetime.now(timezone.utc).isoformat(), "commands": [],
              "limits": {"cpus": 2, "memory_bytes": 2 * 1024**3, "memory_swap_bytes": 2 * 1024**3,
                         "pids": 256, "watchdog_seconds": 1800, "cleanup_seconds": 300}}
    deadline = time.monotonic() + 1800

    def save():
        (evidence / "container-qualification.json").write_text(json.dumps(report, indent=2) + "\n")

    def command(label, arguments, timeout=60, expected=0, cleanup=False):
        remaining = (cleanup_deadline if cleanup else deadline) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Qualification watchdog/cleanup budget expired")
        record = {"label": label, "command": arguments, "started_utc": datetime.now(timezone.utc).isoformat()}
        report["commands"].append(record)
        save()
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=min(timeout, remaining))
        record.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                      finished_utc=datetime.now(timezone.utc).isoformat())
        save()
        if expected is not None and result.returncode != expected:
            raise RuntimeError(label + " failed: " + result.stderr + result.stdout)
        return result

    def inspect_container(cleanup=False):
        result = command("inspect owned container", ["docker", "container", "inspect", name], expected=None, cleanup=cleanup)
        if result.returncode:
            if "No such container" in result.stderr or "No such object" in result.stderr:
                return None
            raise RuntimeError("Container presence is uncertain: " + result.stderr)
        state = json.loads(result.stdout)[0]
        if state["Config"]["Labels"].get("tempo.resource-owner") != owner:
            raise RuntimeError("Container ownership differs; refusing cleanup")
        return state

    def interrupted(_number, _frame):
        raise InterruptedError("Qualification interrupted; cleaning up owned container")

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    cache = None
    attempted = False
    cleanup_deadline = deadline
    lock = open("/tmp/tempo-mac-validation-20261025.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report["validation_lock_acquired"] = True
        report["source_revision"] = command("source revision", ["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.strip()
        report["source_tree"] = command("source tree", ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"]).stdout.strip()
        if command("source status", ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"]).stdout.strip():
            raise RuntimeError("Use a clean disposable checkout")
        daemon = json.loads(command("daemon capacity", ["docker", "info", "--format", "{{json .}}"] ).stdout)
        report["daemon"] = {key: daemon.get(key) for key in ("NCPU", "MemTotal", "OSType", "Architecture", "ServerVersion")}
        if daemon["OSType"] != "linux" or daemon["NCPU"] < 2 or daemon["MemTotal"] < 2 * 1024**3:
            raise RuntimeError("Daemon cannot supply the fixed container limits")
        stats = command("current usage", ["docker", "stats", "--no-stream", "--format", "{{json .}}"])
        current = [json.loads(line) for line in stats.stdout.splitlines() if line.strip()]
        powers = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
        used_memory = 0
        for row in current:
            match = re.fullmatch(r"([0-9.]+)(B|KiB|MiB|GiB)", row["MemUsage"].split(" / ")[0].strip())
            if not match:
                raise RuntimeError("Cannot establish current daemon memory usage")
            used_memory += float(match[1]) * powers[match[2]]
        used_cpu = sum(float(row["CPUPerc"].rstrip("%")) for row in current)
        report["observed_other_usage"] = {"memory_bytes": used_memory, "cpu_percent": used_cpu}
        if used_memory + 2 * 1024**3 > daemon["MemTotal"] or used_cpu + 200 > daemon["NCPU"] * 100:
            raise RuntimeError("Insufficient observed headroom; limits will not be expanded")
        command("pull pinned official image", ["docker", "pull", "--platform", "linux/arm64", image], timeout=900)
        installed = json.loads(command("image identity", ["docker", "image", "inspect", image]).stdout)[0]
        report["image"] = {key: installed[key] for key in ("Id", "RepoDigests", "Os", "Architecture")}
        if installed["Os"] != "linux" or installed["Architecture"] != "arm64":
            raise RuntimeError("Unexpected image OS/architecture")
        cache = Path(tempfile.mkdtemp(prefix="tempo-maia-npm-cache-", dir=evidence.parent))
        (evidence / "container-entry.sh").write_bytes(Path(__file__).with_suffix(".sh").read_bytes())
        arguments = ["docker", "create", "--name", name, "--platform", "linux/arm64", "--init",
                     "--label", "tempo.resource-owner=" + owner, "--label", "tempo.disposable=true",
                     "--cpus", "2", "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "256",
                     "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--user", str(os.getuid()) + ":" + str(os.getgid()),
                     "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m", "--network", "bridge",
                     "--mount", "type=bind,src=" + str(source) + ",dst=/source",
                     "--mount", "type=bind,src=" + str(evidence) + ",dst=/evidence",
                     "--mount", "type=bind,src=" + str(cache) + ",dst=/cache",
                     "--workdir", "/source/workers/maia", "--env", "EXPECTED_NODE=" + version,
                     "--env", "npm_config_cache=/cache", "--env", "npm_config_userconfig=/dev/null",
                     "--env", "npm_config_audit=false", "--env", "npm_config_fund=false",
                     "--env", "npm_config_update_notifier=false", installed["Id"],
                     "sh", "/evidence/container-entry.sh"]
        attempted = True
        command("create bounded container", arguments)
        state = inspect_container()
        limits = state["HostConfig"]
        if (limits["NanoCpus"], limits["Memory"], limits["MemorySwap"], limits["PidsLimit"]) != (2_000_000_000, 2 * 1024**3, 2 * 1024**3, 256):
            raise RuntimeError("Observed container limits differ")
        report["observed_limits"] = {key: limits[key] for key in ("NanoCpus", "Memory", "MemorySwap", "PidsLimit")}
        command("start container", ["docker", "start", name])
        disconnected = False
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("30-minute qualification watchdog expired")
            state = inspect_container()
            if not state:
                raise RuntimeError("Owned container disappeared before results were preserved")
            if (evidence / "dependencies-ready").exists() and not disconnected:
                command("disconnect installation network", ["docker", "network", "disconnect", "bridge", name])
                if inspect_container()["NetworkSettings"]["Networks"]:
                    raise RuntimeError("Installation network remains attached")
                report["test_networks"] = []
                (evidence / "network-disconnected").touch()
                disconnected = True
                save()
            if not state["State"]["Running"]:
                report["container_exit_code"] = state["State"]["ExitCode"]
                report["oom_killed"] = state["State"]["OOMKilled"]
                if state["State"]["ExitCode"] != 0 or state["State"]["OOMKilled"]:
                    raise RuntimeError("Container failed; see stage logs")
                break
            time.sleep(2)
        pure = json.loads((evidence / "pure.json").read_text())
        model = json.loads((evidence / "model.json").read_text())
        artifact = json.loads((evidence / "installed.json").read_text())
        if not disconnected or any(not result["success"] or not result["passed"] or result["failed"] or result["skipped"] for result in (pure, model)):
            raise RuntimeError("Missing or failed owning test cases")
        if not artifact["complete"] or not artifact["cases"]["success"] or not artifact["cases"]["passed"]:
            raise RuntimeError("Installed artifact did not qualify")
        report["cases"] = {"pure": pure, "model": model, "installed": artifact["cases"]}
        report["checks_passed"] = True
    except BaseException as error:
        report["error"] = str(error)
    finally:
        cleanup_deadline = time.monotonic() + 300
        try:
            if attempted:
                state = inspect_container(cleanup=True)
                if state:
                    command("preserve container logs", ["docker", "logs", name], expected=None, cleanup=True)
                    command("remove owned container", ["docker", "rm", "--force", name], cleanup=True)
                if inspect_container(cleanup=True) is not None:
                    raise RuntimeError("Owned container remains")
            report["container_absent"] = True
            if cache:
                shutil.rmtree(cache)
            report["temporary_cache_removed"] = cache is None or not cache.exists()
            report["complete"] = bool(report.get("checks_passed"))
        except BaseException as error:
            report["cleanup_error"] = str(error)
        report["image_retained"] = True
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        lock.close()
    print(json.dumps({key: report.get(key) for key in ("complete", "source_revision", "image_reference", "container_absent", "temporary_cache_removed", "error", "cleanup_error")}))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
