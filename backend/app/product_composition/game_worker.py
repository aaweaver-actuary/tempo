"""Optional worker loop only. The ordinary HTTP/Study application never starts it."""
import signal
from threading import Event


def run_optional_worker(runtime, *, enqueue_due, idle_seconds=1.0,
                        stop_when_idle=False, max_units=None):
    """One process owns its signal handlers, bounded runner and cancellation.

    Composition supplies the durable configured-job trigger. A stopped worker
    never closes Study or a borrowed database pool. Forced termination relies on
    OS process/socket cleanup; no transaction is held over provider HTTP work.
    """
    if not 0.01 <= idle_seconds <= 30:
        raise ValueError("finite idle interval required")
    if max_units is not None and (type(max_units) is not int or not 1 <= max_units <= 10000):
        raise ValueError("max_units must be 1..10000")
    stopped = Event()
    previous = {}
    units = 0

    def stop(signum, frame):
        stopped.set()
        runtime.request_stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, stop)
    try:
        while not stopped.is_set():
            enqueue_due()
            result = runtime.run_once()
            units += 1
            if stop_when_idle and result in {"idle", "stopped", "deferred"}:
                break
            if max_units is not None and units >= max_units:
                break
            if result in {"idle", "stopped", "deferred"}:
                stopped.wait(idle_seconds)
    finally:
        runtime.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return units
