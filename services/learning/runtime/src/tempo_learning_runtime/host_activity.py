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
        self.requests = 0
        self.quiet_until = 0
        self.published_until = 0
        self.publication_failed = True
        self.changed = threading.Event()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def _snapshot(self):
        # Only bounded memory work shares synchronization with essential requests.
        now = self.clock()
        with self.lock:
            self.sessions = {session: until for session, until in self.sessions.items() if until > now}
            sessions = dict(self.sessions)
            if self.quiet_until > now:
                sessions["request_quiet"] = max(sessions.get("request_quiet", 0), self.quiet_until)
            return {"version": 1, "producer": self.producer, "source": self.source, "pid": os.getpid(),
                    "validUntilNs": str(now + POLICY_NS),
                    "pendingRequests": bool(self.requests),
                    "sessions": {session: str(until) for session, until in sessions.items()}}

    def _publish(self):
        snapshot = self._snapshot()
        # The publisher alone owns file I/O. It never holds the memory lock here.
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".activity-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(snapshot, output)
            os.replace(temporary, self.directory / (self.producer + ".json"))
        finally:
            Path(temporary).unlink(missing_ok=True)
        with self.lock:
            self.published_until = int(snapshot["validUntilNs"])
            self.publication_failed = False

    def record(self, session, remaining_ms=3000):
        if self.directory is None:
            raise RuntimeError("Host activity reporting is not configured")
        now = self.clock()
        with self.lock:
            if session not in self.sessions and len(self.sessions) >= 1024:
                raise RuntimeError("Host activity session capacity unavailable")
            self.sessions[session] = max(self.sessions.get(session, 0), now + remaining_ms * 1_000_000)
            available = not self.stopping.is_set() and not self.publication_failed and self.published_until > now
        self.changed.set()
        if not available:
            raise RuntimeError("Host activity publication unavailable; pulse retained for retry")

    @contextmanager
    def request(self):
        if self.directory is None:
            yield
            return
        with self.lock:
            self.requests += 1
        self.changed.set()
        try:
            yield
        finally:
            now = self.clock()
            with self.lock:
                self.requests -= 1
                self.quiet_until = max(self.quiet_until, now + ACTIVITY_NS)
            self.changed.set()

    def start(self):
        if self.directory is None or self.thread is not None or self.stopping.is_set():
            return

        def renew():
            try:
                while not self.stopping.is_set():
                    self.changed.clear()
                    try:
                        self._publish()
                    except OSError:
                        with self.lock:
                            self.publication_failed = True
                        # Retained snapshots expire closed in the host reader.
                        logger.exception("Host activity publication unavailable; managed work must defer")
                    if not self.stopping.is_set():
                        self.changed.wait(5)
            finally:
                self._cleanup()

        try:
            self.thread = threading.Thread(target=renew, name="learning-host-activity", daemon=True)
            self.thread.start()
        except RuntimeError:
            self.thread = None
            logger.exception("Host activity renewal unavailable; managed work must defer when control expires")

    def _cleanup(self):
        # A registered clean shutdown declares only this producer absent. If I/O
        # stalls, its retained record expires; expected sources still deny admission.
        path = self.directory / (self.producer + ".json")
        try:
            if json.loads(path.read_text())["producer"] == self.producer:
                path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            logger.exception("Host activity cleanup unverified; retain the owned snapshot for reconciliation")

    def close(self):
        # Never wait for the optional daemon, including its cleanup or file lock.
        self.stopping.set()
        self.changed.set()
