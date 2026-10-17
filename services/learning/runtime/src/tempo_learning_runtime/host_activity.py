"""Optional same-host admission signal. No database, broker, or Study authority."""
import json
import logging
import os
from pathlib import Path
import tempfile
import sys
import threading
import time
import uuid
from contextlib import contextmanager

ACTIVITY_NS = 3_000_000_000
POLICY_NS = 15_000_000_000
logger = logging.getLogger(__name__)


def default_directory():
    return Path(os.environ.get("TEMPO_STUDY_ACTIVITY_DIRECTORY", str(Path(tempfile.gettempdir()) / "tempo-study-activity-v1")))


def host_activity_clock_ns():
    """Match the native host reader's clock, including macOS suspend time."""
    if sys.platform == "darwin":
        # libuv's hrtime uses the continuous raw clock. Python's default
        # mach_absolute_time clock excludes suspend and diverges after sleep.
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    return time.monotonic_ns()


class HostActivity:
    def __init__(self, directory, *, source=None, clock=host_activity_clock_ns):
        self.directory = Path(directory) if directory is not None else None
        self.producer = str(uuid.uuid4())
        self.source = source or self.producer
        self.clock = clock
        self.sessions = {}
        self.requests = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def _publish(self):
        now = self.clock()
        self.sessions = {session: until for session, until in self.sessions.items() if until > now}
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        snapshot = {"version": 1, "producer": self.producer, "source": self.source, "pid": os.getpid(),
                    "validUntilNs": str(now + POLICY_NS),
                    "pendingRequests": bool(self.requests),
                    "sessions": {session: str(until) for session, until in self.sessions.items()}}
        descriptor, temporary = tempfile.mkstemp(prefix=".activity-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(snapshot, output)
            os.replace(temporary, self.directory / (self.producer + ".json"))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def record(self, session, remaining_ms=3000):
        if self.directory is None:
            raise RuntimeError("Host activity reporting is not configured")
        with self.lock:
            self.sessions[session] = max(self.sessions.get(session, 0), self.clock() + remaining_ms * 1_000_000)
            self._publish()

    @contextmanager
    def request(self):
        if self.directory is None:
            yield
            return
        token = "request_" + uuid.uuid4().hex
        def publish():
            try:
                self._publish()
            except OSError:
                logger.exception("Essential request activity unavailable; retained control expires closed")
        with self.lock:
            self.requests.add(token)
            publish()
        try:
            yield
        finally:
            with self.lock:
                self.requests.discard(token)
                self.sessions[token] = self.clock() + ACTIVITY_NS
                publish()

    def start(self):
        if self.directory is None:
            return

        def publish():
            try:
                with self.lock:
                    self._publish()
            except OSError:
                # Retained snapshots expire closed. Essential Study has no file dependency.
                logger.exception("Host activity publication unavailable; managed work must defer")

        publish()

        def renew():
            while not self.stopping.wait(5):
                publish()

        try:
            self.thread = threading.Thread(target=renew, name="learning-host-activity", daemon=True)
            self.thread.start()
        except RuntimeError:
            self.thread = None
            logger.exception("Host activity renewal unavailable; managed work must defer when control expires")

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=1)
        if self.directory is not None:
            # A registered clean shutdown explicitly declares this producer absent.
            # Crashed producers retain a stale record and require reconciliation.
            with self.lock:
                path = self.directory / (self.producer + ".json")
                try:
                    if json.loads(path.read_text())["producer"] == self.producer:
                        path.unlink()
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, KeyError, TypeError):
                    logger.exception("Host activity cleanup unverified; retain the owned snapshot for reconciliation")
