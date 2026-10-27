"""Optional worker loop only. The ordinary HTTP/Study application never starts it."""
import signal
from threading import Event


def run_optional_worker(runtime, *, enqueue_due, idle_seconds=1.0):
    """One process owns its signal handlers, bounded runner and cancellation.

    Composition supplies the durable configured-job trigger. A stopped worker
    never closes Study or a borrowed database pool. Forced termination relies on
    OS process/socket cleanup; no transaction is held over provider HTTP work.
    """
    if not 0.01 <= idle_seconds <= 30:
        raise ValueError("finite idle interval required")
    stopped = Event()
    previous = {}

    def stop(signum, frame):
        stopped.set()
        runtime.request_stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, stop)
    try:
        while not stopped.is_set():
            enqueue_due()
            if runtime.run_once() in {"idle", "stopped"}:
                stopped.wait(idle_seconds)
    finally:
        runtime.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
