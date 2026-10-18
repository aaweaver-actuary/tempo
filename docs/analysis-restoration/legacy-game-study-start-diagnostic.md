# Proposed helper-start diagnostic — not authorized or launched

Purpose: distinguish Docker creation/readiness from managed wrapper startup
timing before another artifact attempt. The retained failed attempt establishes
neither root cause. The lifecycle cleanup repair does not establish readiness.

Request a **separate two-minute all-in window:60 seconds work,60 seconds verified
cleanup**, one helper only, no retry. Parent coordinates the existing resource
owner. Start only after its explicit grant names the reviewed feature head and
already-local image:
`sha256:a095f09e905ecb00635b80387e17d195560a5752df83d970f94f1ada902ba3e2`.
No image pull/build, uv, dependency download, installation, tests, PostgreSQL,
Redis, browser or live data access is part of this diagnostic.

Use the existing development budget/admission and managed Docker wrapper. Give
the helper a fresh task-owned exact name and owner label. Keep current caps:
1CPU,768MiB memory,768MiB memory+swap (no additional swap),128PIDs,256MiB `/tmp`,
`--init`, read-only source and its new writable evidence directory; no Docker
socket, credential/live-data mount or copied live environment. Use `--network
none` because no connection is needed. Explicit entrypoint `python` prevents
application startup. Do not change the wrapper's30-second startup threshold.

Its only Python payload reads `platform.python_version()`, architecture and the
four cgroup values already checked by the qualifier, writes a timestamped
`started.json`, emits a start marker and flushes, holds for **two seconds**, then
writes/emits an exit marker and returns. This bounded hold exposes Running
without turning the diagnostic into an artifact workload.

Supervision records UTC and monotonic timestamps for image-inspect completion,
managed-wrapper/Docker CLI start and exit, exact-name container state (created,
running, exited/exit code/error), and observed Python markers. Sample the exact
container only at bounded checkpoints, with per-CLI deadlines, and retain stdout,
stderr and ownership journal identity. The host process session is supervised by
the repaired `Deadline`; terminate only that proven owned group on failure.
No broad Docker inventory, daemon restart or unrelated resource cleanup.

Interpretation:

| Evidence | Supported conclusion |
| --- | --- |
| Exact container never visible; CLI still pending at cutoff | Docker create/client path stalled before visible startup; Python/uv/tests did not run. Preserve exact error/timing; no claimed root cause beyond this boundary. |
| Container visible created but no Python start marker | Runtime/start failure or delay; retain State.Error/exit data to locate it. |
| Python markers and clean exit precede wrapper startup failure | Wrapper observation/completion timing is implicated; compare timestamps before any repair. |
| Verified caps, both markers, wrapper exits0 and cleanup confirmed | This one helper launch succeeded; artifact/native work remains unqualified. |

Stop work at60s even if startup is uncertain. The remaining60s must terminate
owned descendants, verify exact owner labels before stop/remove, reconcile an
absent-helper journal through the repaired exact-name path, and independently
verify the owned container and its journal/admission/budget lock absent. Failed
absence verification keeps ownership and is reported as a blocker. Do not retry
or proceed to qualification in the same grant.
