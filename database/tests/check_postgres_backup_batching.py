"""Explicit synthetic backup comparison proof; never accepts an external DSN.

Run through scripts/development-budget.mjs. Timings describe this small fixture,
not production performance. A fetch batch bounds rows, not arbitrary value bytes.
"""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "database"))

from database.tooling import migrate_sqlite_to_postgres as fingerprints
from database.tooling.verify_postgres_backup import compare_backup


# The same Node process must issue startup and reconcile its PID-owned journal.
# Only Docker startup crosses this bridge; BoundedFixture owns provisioning/close.
STARTUP_OWNER = r"""
import { createInterface } from 'node:readline';
import { spawnSync } from 'node:child_process';
const { validateDevelopmentBudgetOwner, withDevelopmentStartup,
  reconcileDevelopmentStartup } = await import(process.argv[1]);
const project = process.argv[2];
// Python handles interruption and completes ownership-checked cleanup first.
for (const name of ['SIGINT', 'SIGTERM']) process.on(name, () => {});
await validateDevelopmentBudgetOwner();
const reply = value => process.stdout.write(JSON.stringify(value) + '\n');
reply({ ready: true });
for await (const line of createInterface({ input: process.stdin })) {
  let startupResult;
  try {
    const request = JSON.parse(line);
    if (request.operation === 'start') {
      const result = withDevelopmentStartup(['postgres'], project,
        () => (startupResult = spawnSync('docker', request.arguments, { encoding: 'utf8', timeout: 15000 })),
        { allocations: { postgres: { memoryMiB: 256 } }, oneOff: true });
      reply({ status: result.status, stdout: result.stdout, stderr: result.stderr });
    } else if (request.operation === 'reconcile') {
      reconcileDevelopmentStartup(project);
      reply({ reconciled: true });
    } else throw new Error('Unknown fixture operation');
  } catch (error) { reply({ error: error.message, stderr: startupResult?.stderr }); }
}
"""


def docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True,
        timeout=15, check=check,
    )


class StartupOwner:
    def __init__(self, project):
        if not os.environ.get("TEMPO_DEVELOPMENT_BUDGET_TOKEN"):
            raise RuntimeError("Run through scripts/development-budget.mjs; ownership is required")
        self.process = subprocess.Popen(
            ["node", "--input-type=module", "-e", STARTUP_OWNER,
             (ROOT / "scripts/development-budget.mjs").as_uri(), project],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            if self.read() != {"ready": True}:
                raise RuntimeError("Development startup owner did not become ready")
        except BaseException:
            try:
                self.close()
            except BaseException as error:
                print(f"CLEANUP FAILED: startup owner: {error}", file=sys.stderr, flush=True)
            raise

    def read(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=60):
                raise TimeoutError("Development startup owner response timed out")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Development startup owner exited; retain any unresolved claim")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"] + ": " + (response.get("stderr") or "").strip())
        return response

    def request(self, operation, **values):
        # Finish/drain an in-flight startup before interruption can begin teardown.
        # Otherwise a late create or stale reply could race the absence check.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            self.process.stdin.write(json.dumps(dict(operation=operation, **values)) + "\n")
            self.process.stdin.flush()
            return self.read()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def close(self):
        self.process.stdin.close()
        try:
            status = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise RuntimeError("Startup owner did not exit; inspect any retained claim")
        finally:
            self.process.stdout.close()
        if status:
            raise RuntimeError(f"Startup owner exited with status {status}")


def inspect_container(reference):
    result = docker("container", "inspect", reference, check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)[0]
    if "No such container" in result.stderr or "No such object" in result.stderr:
        return None
    raise RuntimeError(f"Container absence is unverified: {result.stderr.strip()}")


def require_owned(container, fixture, container_id=None):
    labels = container["Config"]["Labels"]
    if (container["Name"] != "/" + fixture.container
            or labels.get("tempo.contract-owner") != fixture.identity
            or labels.get("tempo.disposable") != "true"
            or labels.get("tempo.resource-owner") != os.environ["TEMPO_DEVELOPMENT_BUDGET_TOKEN"]
            or labels.get("tempo.resource-class") != "development"
            or labels.get("com.docker.compose.project") != fixture.identity
            or labels.get("com.docker.compose.service") != "postgres"
            or container_id is not None and container["Id"] != container_id):
        raise RuntimeError("Fixture identity/ownership changed; refusing teardown")


def verify_limits(container):
    limits = container["HostConfig"]
    if (limits["NanoCpus"], limits["Memory"], limits["MemorySwap"], limits["PidsLimit"]) != (
            0, 268_435_456, 268_435_456, 256):
        raise RuntimeError("PostgreSQL fixture resource limits differ from the allocation")
    bindings = container["NetworkSettings"]["Ports"]
    if (set(bindings) != {"5432/tcp"} or len(bindings["5432/tcp"]) != 1
            or bindings["5432/tcp"][0]["HostIp"] != "127.0.0.1"):
        raise RuntimeError("Fixture must expose only a random loopback PostgreSQL port")
    if limits["Tmpfs"] != {"/var/lib/postgresql": "rw,size=256m"}:
        raise RuntimeError("Fixture temporary storage differs from the bounded tmpfs")
    if any(mount["Type"] != "tmpfs" for mount in container["Mounts"]):
        raise RuntimeError("Fixture has a non-ephemeral mount")
    print(json.dumps(dict(
        image_id=container["Image"], container_id=container["Id"],
        cpus=limits["NanoCpus"] / 1e9, memory_bytes=limits["Memory"],
        memory_swap_bytes=limits["MemorySwap"], pids_limit=limits["PidsLimit"],
        ports=bindings, tmpfs=limits["Tmpfs"], mounts=container["Mounts"],
    )), flush=True)


def synthetic_row(index):
    return (
        f"group-{index // 1000:02d}", index % 1000,
        None if index % 7 == 0 else index - 1250, index / 8.0,
        bytes([index % 256, (index // 256) % 256]),
        "x" * 4080 + f"雪-é-{index:04d}",
    )


def seed(database):
    database.execute("CREATE TABLE batch_empty (id integer PRIMARY KEY)")
    database.execute(
        "CREATE TABLE batch_rows (group_key text NOT NULL, row_key integer NOT NULL, "
        "nullable_value integer, floating_value double precision NOT NULL, "
        "binary_value bytea NOT NULL, text_value text NOT NULL, "
        "PRIMARY KEY (group_key, row_key))"
    )
    with database.transaction():
        with database.cursor() as cursor:
            with cursor.copy("COPY batch_rows FROM STDIN") as copy:
                for index in reversed(range(2501)):
                    copy.write_row(synthetic_row(index))


def require_mismatch(source, restored, message):
    try:
        compare_backup(source, restored)
    except RuntimeError as error:
        if not str(error).startswith(message):
            raise
        print("Expected rejection: " + str(error), flush=True)
    else:
        raise AssertionError("Backup comparison accepted an injected mismatch: " + message)


def verify_comparison(source, restored):
    expected = hashlib.sha256()
    for index in range(2501):
        fingerprints.update_row_digest(expected, synthetic_row(index))
    expected_rows = (2501, expected.hexdigest())
    expected_empty = (0, hashlib.sha256().hexdigest())
    results = {}
    original_batch_size = fingerprints.FINGERPRINT_BATCH_SIZE
    try:
        for batch_size in (100, 1000):
            fingerprints.FINGERPRINT_BATCH_SIZE = batch_size
            started = time.perf_counter()
            outcomes = []
            for database in (source, restored):
                empty = fingerprints.destination_fingerprint(database, "batch_empty", ["id"], ["id"], set())
                populated = fingerprints.destination_fingerprint(
                    database, "batch_rows",
                    ["group_key", "row_key", "nullable_value", "floating_value", "binary_value", "text_value"],
                    ["group_key", "row_key"], {"group_key"},
                )
                if empty != expected_empty or populated != expected_rows:
                    raise AssertionError(f"Incomplete or changed fingerprint: {empty}, {populated}")
                outcomes.append(dict(empty=empty, populated=populated))
            results[batch_size] = outcomes
            print(json.dumps(dict(batch_size=batch_size, seconds=time.perf_counter() - started,
                                  fingerprints=outcomes)), flush=True)
        if results[100] != results[1000]:
            raise AssertionError("Real server cursor batching changed counts or digests")
    finally:
        fingerprints.FINGERPRINT_BATCH_SIZE = original_batch_size

    compare_backup(source, restored)
    tail = synthetic_row(2500)
    restored.execute("UPDATE batch_rows SET text_value=%s WHERE group_key=%s AND row_key=%s",
                     ("changed-tail", tail[0], tail[1]))
    require_mismatch(source, restored, "Restore row mismatch in batch_rows:")
    restored.execute("UPDATE batch_rows SET text_value=%s WHERE group_key=%s AND row_key=%s",
                     (tail[5], tail[0], tail[1]))
    compare_backup(source, restored)
    restored.execute("DROP TABLE batch_empty")
    require_mismatch(source, restored, "Restore schema mismatch:")


def main():
    specification = importlib.util.spec_from_file_location("backup_batching_fixture", ROOT / "database/ownership/fixture.py")
    fixture_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(fixture_module)
    fixture = fixture_module.BoundedFixture()
    owner = None
    container_id = None
    creation_attempted = False
    primary_error = None

    def bounded_docker(*arguments, check=True):
        nonlocal creation_attempted, container_id
        if arguments[0] == "run":
            creation_attempted = True
            arguments = (
                "run", "--memory-swap", "256m", "--pull=never",
                "--label", "tempo.resource-owner=" + os.environ["TEMPO_DEVELOPMENT_BUDGET_TOKEN"],
                "--label", "tempo.resource-class=development",
                "--label", "com.docker.compose.project=" + fixture.identity,
                "--label", "com.docker.compose.service=postgres", *arguments[1:],
            )
            response = owner.request("start", arguments=arguments)
            container_id = response["stdout"].strip()
            return subprocess.CompletedProcess(["docker", *arguments], response["status"],
                                               response["stdout"], response["stderr"])
        if arguments[0] == "rm":
            current = inspect_container(fixture.container)
            if current is None:
                raise RuntimeError("Fixture disappeared before ownership-checked teardown")
            require_owned(current, fixture, container_id)
            # Remove the inspected immutable identity, never a replacement by name.
            arguments = (*arguments[:-1], current["Id"])
        return docker(*arguments, check=check)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Backup batching proof interrupted")

    previous_int_handler = signal.getsignal(signal.SIGINT)
    previous_term_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        owner = StartupOwner(fixture.identity)
        fixture_module.IMAGE = docker("image", "inspect", fixture_module.IMAGE, "--format", "{{.Id}}").stdout.strip()
        fixture_module.docker = bounded_docker
        print("Owned PostgreSQL fixture: " + fixture.identity + "; image " + fixture_module.IMAGE, flush=True)
        fixture.start()
        container = inspect_container(fixture.container)
        require_owned(container, fixture, container_id)
        verify_limits(container)
        with ExitStack() as connections:
            source = connections.enter_context(fixture.admin("learning"))
            restored = connections.enter_context(fixture.admin("content"))
            version = source.execute("SHOW server_version_num").fetchone()[0]
            if version != "180006":
                raise RuntimeError(f"Expected PostgreSQL 18.6, got server_version_num={version}")
            seed(source)
            seed(restored)
            verify_comparison(source, restored)
        maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_bytes = maximum_rss if sys.platform == "darwin" else maximum_rss * 1024
        print(json.dumps(dict(os=platform.system(), postgres_version=version,
                              observed_peak_python_process_memory_mib=rss_bytes / 1024 ** 2,
                              memory_scope="process lifetime high-water mark; not an enforced verifier limit")), flush=True)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        cleanup_errors = []
        try:
            if creation_attempted:
                current = inspect_container(fixture.container)
                if current is not None:
                    require_owned(current, fixture, container_id)
                    container_id = current["Id"]
                    fixture.started = True  # A timed-out create may have succeeded.
                    fixture.close()
                for reference in filter(None, (fixture.container, container_id)):
                    if inspect_container(reference) is not None:
                        raise RuntimeError("Owned container remains: " + reference)
                for kind in ("container", "volume"):
                    remaining = docker(kind, "ls", "--quiet", "--filter", "label=tempo.contract-owner=" + fixture.identity,
                                       *(["--all"] if kind == "container" else [])).stdout.strip()
                    if remaining:
                        raise RuntimeError("Owned disposable resources remain: " + remaining)
            if owner is not None:
                if owner.request("reconcile") != {"reconciled": True}:
                    raise RuntimeError("Development ownership reconciliation is unverified")
                print("Verified disposable absence and development ownership reconciliation: " + fixture.identity, flush=True)
        except BaseException as error:
            cleanup_errors.append(error)
        finally:
            if owner is not None:
                try:
                    owner.close()
                except BaseException as error:
                    cleanup_errors.append(error)
            signal.signal(signal.SIGTERM, previous_term_handler)
            signal.signal(signal.SIGINT, previous_int_handler)
        for error in cleanup_errors:
            print(f"CLEANUP FAILED: {error}; retain unresolved ownership", file=sys.stderr, flush=True)
        if cleanup_errors and primary_error is None:
            raise RuntimeError("Disposable cleanup failed") from cleanup_errors[0]


if __name__ == "__main__":
    main()
