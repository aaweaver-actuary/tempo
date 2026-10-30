"""One no-init/init mechanism comparison on two verified disposable containers.

Run with the deployed, already-present image ID in
TEMPO_POSTGRES_INIT_TEST_IMAGE and the existing Docker context in
TEMPO_POSTGRES_INIT_TEST_CONTEXT, using make python-file FILE=<this file>.
No image pull, port, production mount, password or live command is used.
The ordinary backend suite discovers this file; no dedicated qualifier is added.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
BOOT = """initdb -D /var/lib/postgresql/owned-data --auth-local=trust --auth-host=reject --no-locale >/tmp/initdb.log
exec postgres -D /var/lib/postgresql/owned-data -c listen_addresses= -c unix_socket_directories=/tmp -c shared_buffers=32MB -c max_connections=16
"""
ORPHAN = """mkfifo /tmp/orphan-release
(
  trap 'status=$?; trap - EXIT; printf "%s\\n" "$status" > /tmp/orphan-outcome; exit "$status"' EXIT
  printf '%s\\n' "$BASHPID" > /tmp/orphan-pid
  read -r release < /tmp/orphan-release
  exit 2
) >/tmp/orphan.stdout 2>/tmp/orphan.stderr &
printf '%s\\n' "$!"
"""


def _docker(*arguments, check=True, timeout=20):
    context = os.environ.get("TEMPO_POSTGRES_INIT_TEST_CONTEXT")
    command = ["docker", *(["--context", context] if context else []), *arguments]
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"Docker command failed: {arguments[0]}: {result.stderr}")
    return result


def _metadata(container_id):
    return json.loads(_docker("inspect", container_id).stdout)[0]


def _owned(container_id, owner, image):
    current = _metadata(container_id)
    assert current["Id"] == container_id
    assert current["Image"] == image
    assert current["Config"]["Labels"]["tempo.owner"] == owner
    assert current["Config"]["Labels"]["tempo.disposable"] == "true"
    assert current["HostConfig"]["NetworkMode"] == "none"
    assert not current["HostConfig"].get("PortBindings")
    assert all(mount["Type"] == "tmpfs" for mount in current["Mounts"])
    assert current["HostConfig"]["Memory"] == 384 * 1024 * 1024
    assert current["HostConfig"]["NanoCpus"] == 0
    assert current["HostConfig"]["PidsLimit"] == 64
    assert not any("PASSWORD=" in entry for entry in current["Config"]["Env"])
    return current


def _logs(container_id):
    result = _docker("logs", "--timestamps", container_id)
    return result.stdout, result.stderr


def _wait_logs(container_id, predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while True:
        streams = _logs(container_id)
        if predicate("\n".join(streams)):
            return streams
        if time.monotonic() >= deadline:
            raise AssertionError("Finite PostgreSQL log observation expired")
        time.sleep(0.1)


def _proc(container_id, pid):
    status = _docker("exec", container_id, "cat", f"/proc/{pid}/status").stdout
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    return {key: fields[key].strip() for key in ("Name", "Pid", "PPid", "State")}


def _wait_reaped_exit_two(container_id, pid):
    """Observe the actor's EXIT status and disappearance, without init logging.

    The identical actor's normal exit 2 is independently recorded by PostgreSQL
    in the baseline. Some Docker init binaries omit verbose child-reap logs.
    """
    deadline = time.monotonic() + 10
    command = (f"if [[ -f /tmp/orphan-outcome && ! -e /proc/{pid} ]]; then "
               "IFS= read -r status < /tmp/orphan-outcome; printf '%s' \"$status\"; "
               "else printf waiting; fi")
    while True:
        value = _docker("exec", container_id, "bash", "-c", command).stdout
        if value == "2":
            return
        assert value == "waiting", "Artificial orphan did not report normal exit 2"
        assert time.monotonic() < deadline, "Init did not reap the artificial orphan"
        time.sleep(0.05)


class HeldClient:
    """One psql process/connection; failure never creates a replacement client."""
    def __init__(self, container_id):
        context = os.environ.get("TEMPO_POSTGRES_INIT_TEST_CONTEXT")
        self.process = subprocess.Popen(
            ["docker", *(["--context", context] if context else []), "exec", "-i",
             container_id, "psql", "-X", "-qAt", "-w", "-v", "ON_ERROR_STOP=1",
             "-h", "/tmp", "-U", "postgres", "-d", "postgres"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.pending = b""
        self.errors = b""

    def query(self, sql, seconds=8):
        marker = "done_" + uuid.uuid4().hex
        try:
            self.process.stdin.write((sql + ";\n\\echo " + marker + "\n").encode())
            self.process.stdin.flush()
        except BrokenPipeError as error:
            raise RuntimeError("The original held client is disconnected") from error
        deadline = time.monotonic() + seconds
        lines = []
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                while b"\n" in self.pending:
                    line, self.pending = self.pending.split(b"\n", 1)
                    value = line.decode(errors="replace")
                    if value == marker:
                        return lines
                    lines.append(value)
                if time.monotonic() >= deadline:
                    raise RuntimeError("Original held client timed out: " + self.errors.decode(errors="replace"))
                for key, _ in selector.select(min(0.2, deadline - time.monotonic())):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data == "stdout":
                        self.pending += chunk
                    else:
                        self.errors += chunk
        raise RuntimeError("Original held client closed: " + self.errors.decode(errors="replace"))

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b"\\q\n")
                self.process.stdin.flush()
            except BrokenPipeError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()  # This task's local Docker client only.
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except BrokenPipeError:
                pass


def _case(image, owner, init_enabled, evidence):
    name = owner + ("-init" if init_enabled else "-baseline")
    report = {"init": init_enabled, "owner": owner, "container_name": name,
              "image_id": image, "cleanup_verified": False}
    container_id = None
    held = None
    try:
        assert not _docker("ps", "-aq", "--filter", f"name=^/{name}$").stdout.strip()
        arguments = ["run", "--detach", "--pull=never", "--name", name,
            "--label", "tempo.owner=" + owner, "--label", "tempo.disposable=true",
            "--network=none", "--memory=384m", "--memory-swap=384m",
            "--pids-limit=64", "--read-only", "--user=postgres",
            "--tmpfs", "/var/lib/postgresql:rw,nosuid,nodev,size=256m,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
            "--env", "TINI_VERBOSITY=3", "--entrypoint", "bash"]
        if init_enabled:
            arguments.append("--init")
        container_id = _docker(*arguments, image, "-euc", BOOT).stdout.strip()
        report["container_id"] = container_id
        current = _owned(container_id, owner, image)
        assert bool(current["HostConfig"].get("Init")) == init_enabled
        _wait_logs(container_id, lambda text: "database system is ready to accept connections" in text,
                   seconds=45)
        report["pid1"] = _proc(container_id, 1)
        assert report["pid1"]["Name"] == ("docker-init" if init_enabled else "postgres")
        held = HeldClient(container_id)
        seeded = held.query("CREATE TABLE init_supervision_sentinel(value text PRIMARY KEY); "
            "INSERT INTO init_supervision_sentinel VALUES('committed-sentinel'); "
            "SELECT pg_backend_pid() || ':' || value FROM init_supervision_sentinel")
        identity = seeded[-1]
        backend_pid = int(identity.split(":", 1)[0])
        postmaster_pid = int(_proc(container_id, backend_pid)["PPid"])
        report.update(held_backend_pid=backend_pid, postmaster_pid=postmaster_pid,
                      sentinel_before=identity.split(":", 1)[1])
        assert postmaster_pid != 1 if init_enabled else postmaster_pid == 1
        if init_enabled:
            assert _proc(container_id, postmaster_pid)["PPid"] == "1"

        # Authority/identity is checked immediately before the one injection.
        _owned(container_id, owner, image)
        orphan_pid = int(_docker("exec", container_id, "bash", "-c", ORPHAN).stdout.strip())
        deadline = time.monotonic() + 5
        while True:
            adopted = _proc(container_id, orphan_pid)
            if adopted["PPid"] == "1":
                break
            assert time.monotonic() < deadline, "Artificial child was not adopted"
            time.sleep(0.05)
        report["adopted_orphan"] = adopted
        _owned(container_id, owner, image)
        _docker("exec", container_id, "bash", "-c", "echo release > /tmp/orphan-release")

        if not init_enabled:
            signature = f"untracked child process (PID {orphan_pid}) exited with exit code 2"
            _wait_logs(container_id, lambda text: signature in text and "reinitializing" in text)
            try:
                held.query("SELECT pg_backend_pid() || ':' || value FROM init_supervision_sentinel")
            except RuntimeError as error:
                report["held_connection_interrupted"] = True
                report["held_connection_error"] = str(error)
            else:
                raise AssertionError("Baseline did not interrupt the original held connection")
            _wait_logs(container_id, lambda text: text.count("database system is ready to accept connections") >= 2)
            fresh = _docker("exec", container_id, "psql", "-X", "-qAt", "-w", "-h", "/tmp",
                "-U", "postgres", "-d", "postgres", "-c", "SELECT value FROM init_supervision_sentinel").stdout.strip()
            assert fresh == "committed-sentinel"
            report["fresh_post_recovery_sentinel"] = fresh
            report["baseline_mechanism_reproduced"] = True
        else:
            _wait_reaped_exit_two(container_id, orphan_pid)
            after = held.query("SELECT pg_backend_pid() || ':' || value FROM init_supervision_sentinel")[-1]
            assert after == identity, "Original backend identity or committed sentinel changed"
            report.update(held_connection_retained=True, sentinel_after=after.split(":", 1)[1],
                          held_backend_after=int(after.split(":", 1)[0]), orphan_reaped=True)
            text = "\n".join(_logs(container_id))
            assert "untracked child process" not in text and "reinitializing" not in text
            assert text.count("database system is ready to accept connections") == 1
        report["orphan_outcome_marker"] = _docker("exec", container_id, "cat", "/tmp/orphan-outcome").stdout.strip()
        assert report["orphan_outcome_marker"] == "2"
        held.close()
        held = None
        _owned(container_id, owner, image)
        _docker("stop", "--time", "15", container_id, timeout=20)
        stopped = _owned(container_id, owner, image)
        text = "\n".join(_logs(container_id))
        report["shutdown"] = {key: stopped["State"][key] for key in ("Running", "ExitCode", "OOMKilled")}
        report["shutdown"]["graceful_log"] = "database system is shut down" in text
        assert not stopped["State"]["Running"] and stopped["State"]["ExitCode"] == 0
        assert not stopped["State"]["OOMKilled"] and report["shutdown"]["graceful_log"]
        assert "received fast shutdown request" in text, "Shutdown signal was not forwarded"
        return report
    except BaseException as error:
        report["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if held is not None:
            held.close()
        # Resolve uncertain create outcomes by unique name; never prune resources.
        found = _docker("ps", "-aq", "--no-trunc", "--filter", f"name=^/{name}$").stdout.strip()
        if found:
            current = _owned(found, owner, image)
            report["container_id"] = found
            stdout, stderr = _logs(found)
            (evidence / (name + ".stdout.log")).write_text(stdout)
            (evidence / (name + ".stderr.log")).write_text(stderr)
            if current["State"]["Running"]:
                report["forced_cleanup"] = True
                _docker("rm", "--force", found)
            else:
                _docker("rm", found)
        assert not _docker("ps", "-aq", "--filter", f"name=^/{name}$").stdout.strip()
        report["cleanup_verified"] = True
        (evidence / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")


def test_orphan_exit_two_is_reaped_by_init_without_postgres_recovery():
    """Artificial-orphan mechanism; never historical executable attribution."""
    selected = os.environ.get("TEMPO_POSTGRES_INIT_TEST_IMAGE", "postgres:18.6-trixie")
    inspected = _docker("image", "inspect", selected, check=False)
    if inspected.returncode:
        pytest.skip("The explicitly selected existing PostgreSQL image is unavailable; no pull attempted")
    image = json.loads(inspected.stdout)[0]["Id"]
    if selected.startswith("sha256:"):
        assert selected == image
    owner = "tempo-pg-init-" + uuid.uuid4().hex
    evidence = Path(os.environ.get("TEMPO_INIT_SUPERVISION_EVIDENCE",
        str(ROOT / "test-results/postgres-init-supervision" / owner)))
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    baseline = _case(image, owner, False, evidence)
    assert baseline["baseline_mechanism_reproduced"], "Inconclusive baseline; do not claim mitigation"
    fixed = _case(image, owner, True, evidence)
    assert fixed["held_connection_retained"] and fixed["orphan_reaped"]
    assert baseline["cleanup_verified"] and fixed["cleanup_verified"]
