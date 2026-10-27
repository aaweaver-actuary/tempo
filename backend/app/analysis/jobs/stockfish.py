"""Infrastructure adapter for #497's owned, one-shot JSON process protocol."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from uuid import uuid4

from app.contracts.product_v02.analysis import EngineContext, EngineEvidence


class EngineBridgeUnavailable(RuntimeError):
    """Safe reason only; never raw process output, configured paths or payloads."""


class StockfishBridge:
    """Synchronous EngineEvaluationPort; invoke only in optional bounded work.

    Trusted configuration selects installed executables and the published script.
    Every invocation owns a new process group; timeout/stop terminates only it.
    The worker owns UCI/engine cleanup and every score/uncertainty decision.
    """
    def __init__(self, *, node: str, script: str, executable: str, timeout_seconds: float = 18):
        if not 0 < timeout_seconds <= 20:
            raise ValueError("engine_bridge_requires_finite_timeout")
        for path in (node, script, executable):
            if not Path(path).is_absolute() or not Path(path).is_file():
                raise ValueError("engine_bridge_requires_explicit_existing_paths")
        self.node, self.script, self.executable = node, script, executable
        self.timeout_seconds = timeout_seconds
        self._unit = threading.Lock()

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.communicate(timeout=.8)
            except subprocess.TimeoutExpired:
                pass
        # A fresh session is owned solely by this request. Also remove an orphan
        # engine if an abnormal bridge exit prevented #497's normal close path.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=.5)
        except subprocess.TimeoutExpired:
            raise EngineBridgeUnavailable("engine_cleanup_unverified") from None

    def evaluate(self, context: EngineContext, candidates: tuple[str, ...], *, cancellation=None) -> EngineEvidence:
        if not self._unit.acquire(blocking=False):
            raise EngineBridgeUnavailable("engine_busy")
        try:
            return self._evaluate(context, candidates, cancellation=cancellation)
        finally:
            self._unit.release()

    def _evaluate(self, context, candidates, *, cancellation):
        if cancellation is not None and cancellation.is_set():
            raise EngineBridgeUnavailable("engine_cancelled")
        identity = "repair:" + uuid4().hex
        payload = json.dumps(dict(request_id=identity, context=context.model_dump(mode="json"),
                                  candidates=list(candidates)), separators=(",", ":")).encode()
        if len(payload) > 32768:
            raise EngineBridgeUnavailable("engine_request_limit")
        process = None
        try:
            process = subprocess.Popen([self.node, self.script], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
                env={"PATH":"/usr/bin:/bin", "TEMPO_STOCKFISH_EXECUTABLE":self.executable})
            deadline = time.monotonic() + self.timeout_seconds
            first = True
            while True:
                if cancellation is not None and cancellation.is_set():
                    raise EngineBridgeUnavailable("engine_cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EngineBridgeUnavailable("engine_timeout")
                try:
                    output, _ = process.communicate(input=payload if first else None,
                                                    timeout=min(.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if process.returncode != 0 or len(output) > 32769:
                raise EngineBridgeUnavailable("engine_worker_unavailable")
            result = json.loads(output)
            if result.get("request_id") != identity:
                raise EngineBridgeUnavailable("engine_response_identity_mismatch")
            if result.get("status") not in {"ok", "inconclusive", "unavailable", "cancelled"}:
                raise EngineBridgeUnavailable("engine_worker_unavailable")
            evidence = EngineEvidence.model_validate_json(json.dumps(result["evidence"]))
            if (evidence.evidence_id != identity or evidence.context != context
                    or evidence.requested_candidates != candidates):
                raise EngineBridgeUnavailable("engine_response_context_mismatch")
            return evidence
        except EngineBridgeUnavailable:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise EngineBridgeUnavailable("engine_bridge_unavailable") from None
        finally:
            if process is not None:
                self._stop(process)
