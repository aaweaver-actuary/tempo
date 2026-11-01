# Packaged Games collection qualification

This is the explicit `games-collection-release` shared suite in `components.json`.
It owns synthetic SQL setup/readback for the collection release and reuses the
existing `test_product_game_lapse_postgres.owned_postgres` fixture for ordered
migrations, resource identity and exact cleanup. No second migration or database
lifecycle owner is created. The provider HTTP fixture stays with backend
composition tests; production modules come exclusively from the pinned image.

Run through `node tools/component-build/test-lane.mjs --suite games-collection-release`
with `TEMPO_COLLECTION_TEST_IMAGE` set to an immutable image ID and the supported
backend interpreter selected by `TEMPO_COMPONENT_PYTHON`. An explicit
`TEMPO_COLLECTION_TEST_REVISION` may name the original image source when every
Dockerfile COPY input still matches; no image identity is relabeled. Missing or
mismatched inputs fail this explicit suite. General backend test discovery does
not implicitly build or require a packaged release image.

See the [qualification receipt](../../../docs/validation/task-g-collection.md)
and [operator boundary](../../../scripts/README.md#games-collection-operator).

## Explicit Study recovery acceptance

`test_study_recovery_packaged.py` reuses the source-bound feedback assembly's
image, roles, actual runtime identities, network, migration and cleanup owner.
It is a separate explicit selection; collection-only qualification does not
inherit the 30-transition recovery target.
The registered `study-recovery` suite uses `qualify_recovery.py` to bind host
fixture imports to this checkout before invoking pytest. The component runner
supplies the existing managed Docker environment, admission and cleanup journal.
Run `node tools/component-build/test-lane.mjs --suite study-recovery`; a plain
pytest command does not establish managed resource admission.

The recovery wrapper selects the existing PostgreSQL fixture's explicit
`study-recovery-assembly` profile: 384 MiB memory and one nonexternal,
task-owned labelled data volume. The separate `study-recovery` diagnostic
profile remains 1 GiB on an owned volume. The default native profile remains
384 MiB with 512 MiB tmpfs.
The disk-backed profile keeps database/WAL storage out of the memory cap;
it does not raise any application transaction deadline. Allocation, exact
mount/label checks, memory evidence and container-before-volume cleanup are
retained in the fixture receipt. Build and run it under the existing resource
admission workflow. The recovery-only allocation is PostgreSQL 384 MiB,
API 192 MiB, foreground 384 MiB, background 448 MiB, Games producer 512 MiB,
Redis 64 MiB and web 64 MiB: exactly the existing 2,048 MiB development budget.
No global service budget changes. Foreground/background concurrency remains 2/1.
Cgroup current/peak/events, process RSS and container OOM/exit state are retained.
These finite candidate caps differ from installed unbounded API/foreground
roles; a passing allocation check is not memory or performance proof.

The synthetic workload preserves ten selected repertoires, 8,268 legal authored
routes, 242,235 authored plies and 94,929 executable graph uses. Physical cards
use the existing content identity and production opening-graph builder; parent
links, learner-ending segments and decision indices are checked through the
real opening frontier. The retained custom control requires two 7-ply prefixes
where the observed first repertoire had a 5-ply maximum. All other maxima
match. Valid segment parity yields 242,138 graph plies versus the observed
242,140. The validated baseline has 37,027 repertoire memberships versus 36,258
observed (+2.12%); no inert rows pad the counts. These differences remain in the
receipts. A standard-root occurrence follows a relevant authored prefix before
its first own departure; a distinct custom-root control is also retained.
576 executable mature opening root reviews supply the ordinary queue workload.
Each injected source is admitted through a fresh controlled 60-second window
and configuration revision committed by the existing Games owner. Those explicit
fixture admissions occur between measured reviews and are retained in the report;
no completed provider job is reopened or forged.

Each mode uses a fresh owned installation with the same deterministic content
baseline. The browser performs 30 actual UI reviews and measures submission,
exact persisted receipt, and the distinct interactive successor with one
`performance.now()` clock. The receipt boundary is after the exact persisted
receipt clone is parsed and before returning that response to the application.
The successor must be present in an actual queue response, have a distinct
committed head origin, and show the matching current card position on Study's
own interactive board. Review/acquisition request failures and timeout responses
fail the healthy run. Native readback after the endpoint checks the exact
review receipt and monotonically newer committed issuance. Three separate
transitions must enter explicit preparation; actual local preparer calls,
retained original effects/settlement and admitted daily-queue background progress
are checked. Every sample and failure remains in the receipts. The first measured transition is already warm from the preceding packaged journey; initial fresh-browser acquisition is recorded separately, without claiming an isolated cold-server measurement.

The old setup collector's synthetic quiet-admission seam is separated from the
measured run. Its recovery phase reconstructs the real managed worker with the
same verified image/container/boot and delegates to the original production
admission callback. During one genuine visibility pause between measured
reviews, the OFF run requires an actually captured/compared opponent-gap source
from that managed worker; admission grants and durable job/source state are
recorded. ON records whether the normal policy pauses the optional producer.
The ordinary Celery workers and daily-queue admission remain production code.
Explicit provider-input fixture staging for the three actionable transitions is
labelled separately and never counted as background admission/capacity proof.
No new mode/runtime identity is invented. Separate fault/currentness evidence
remains part of the coordinator's G2 verdict. No installed or genuine-user
recovery is implied.

For a coordinated run, set `TEMPO_STUDY_RECOVERY_CONTENT` to the saved version-2
authored publication checkpoint. Its generator hash must match this checkout.
Only authored lines/cards/graph payloads are reused, with repertoire labels
remapped and content-derived physical IDs preserved. Findings, owner captures,
currentness, accepted effects, reviews and issuance are recomputed by the actual
candidate. The first pure control is replaced by the actual retained disposable
control; its geometry is recorded. Without that input, the deterministic generator
runs once per mode pair and caches only authored fixture payloads in the test
process. This checkpoint is content setup, never a qualification receipt.

The #668 prerequisite variant reuses that same running assembly before the large
fixture is added. Two synthetic existing mature tactic definitions become due;
only the production queue owner admits/issues them. While the real browser waits
and the optional background worker is paused, one essential Celery child exits
just after a real committed queue slice and before acknowledgment. The existing
worker must respawn/recover and publish the same durable queue generation.
The fixture then captures a real qualifying source, invalidates its certificate
through the owner, pauses its collector with managed mode unchanged, and requires
actual unrelated browser save/replay plus a playable successor. Exact integrity
maintenance claims run the installed scan handlers to restore authority; no
successful certificate, task, effect, issuance or review is manufactured. The
same original delivery must reach real target issuance and clean browser
settlement. Each pause/process/resource belongs to the verified disposable
assembly and is covered by its existing teardown. The separate unchanged
thirty-transition loop retains the representative latency targets and raw samples.
New semantic feedback sources use the actual UTC clock before capture and
qualification, including the three ten-repertoire sources. The earlier settled
source reconciliation may accelerate its collection schedule, but that clock
must not carry into a new determination ahead of actual browser issuance. Held
retry waits for the original retained retry deadline within the existing 45s
orchestration bound; it never advances the original command's semantic time.
The fixture clock continues with actual wall time after each snapshot, allowing
the continuous collector's original uncertainty/held retries to become due.

The recovery foreground worker uses Celery's supported embedded beat with its
schedule file under `/tmp`, retaining two prefork command workers and the same
384MiB container cap. This runs the actual checked-in one-second essential
periodic fallback after a consumed coalesced wake loses its child, rather than
assuming a retry hint can replace the scheduler. Startup is asserted from the
actual beat log; process/cgroup memory and OOM evidence cover the added process.
The background worker has no embedded beat. No extra service or budget is added.
The one-shot crash marker also lives in that worker's existing `/tmp` mount;
the host reads it through bounded `docker exec`. The provider/source fixture
mount stays read-only.

Collector startup follows protected browser acquisition and API/foreground role
verification. Only its own real runtime/profile verification, managed-mode
commit and provider release run inside the unchanged 45-second collector
admission window; unrelated setup does not consume that window.

The 576-card recovery backlog selects actual generated opening prefixes and
response segments. A response starts with an opponent cue before the learner's
legal reply; the real browser plays only trained-color decisions. Shared root
prefixes are deliberately deduplicated and cannot alone supply that backlog.
The ten complete configured repertoires and all persisted shape assertions remain
unchanged; no inert cards or extra routes are added to reach the count.

The browser measurement correlates the original attempt POST and its exact
`review-attempt:<attempt_id>` command identity with the supported persisted
card/queue/review receipt. The public receipt does not echo `attempt_id`. After
the measured boundary the existing host check reads that immutable attempt's
owner receipt and requires exact result JSON, matching review/card/queue and both
authoritative issuance origins. Asynchronous responses must retain the original
command identity; these checks do not invent an API field or weaken settlement.

The seven-role recovery PostgreSQL profile inherits the pinned production
image's connection default, matching the checked-in production composition.
The smaller native-owner fixture retains its explicit 16-connection cap. Actual
maximum and reserved-slot settings are read from the owned database into
`fixture.json`; the assembly keeps its 384MiB memory cap, 128-PID limit, 32MiB
shared buffers and all existing transaction/acquisition deadlines. This aligns
the test profile without changing production pool settings or claiming a leak.

Post-measurement readback checks the complete command owner's stored response
against the exact public browser receipt, including its recombination annotation,
and binds its original card/attempt/queue payload. The Learning attempt receipt
is compared exactly after removing only that known after-save annotation; its
prepared continuation and every other field remain required. This follows the
existing adapter's two receipt contracts rather than discarding unknown fields.

Live synthetic provider input updates are published by writing a complete sibling
file and atomically replacing `provider.json`, using the same boundary as the
existing ready markers. Readers observe one complete snapshot; parse failures
remain real failures rather than being caught or retried. Initial prelaunch
creation and all provider/owner/receipt assertions remain unchanged.

Live provider snapshots are now published inside each existing role's Linux
`/tmp` through verified Docker input, atomic replacement and checksum readback.
Assembly roles bootstrap once from their immutable launch environment; subsequent
helper reads use that guest snapshot. The separate bounded collect-only fixture
may bootstrap once from its prelaunch host file. The host provider copy is
evidence, not live consumer authority. Paused roles may defer only during the
held case while both recovery release markers are absent; the next default
publication must update every running recovery role before either marker is
released. The existing child crash preserves `/tmp`; selected flows never restart
a stopped collector. No service, volume, credentials or resource cap is added.

The settled-source reconciliation replay advances its controlled schedule from
the actual last completed reconciliation checkpoint, matching production's
completion-based interval. New semantic sources still reset to actual UTC before
qualification; no original command or receipt timestamp is rewritten.

The three full-scope controlled collection calls at transitions4/14/24 execute
in the existing448MiB optional background role, verified running and unpaused.
They use the same collect-only owner/HTTP fixture and writer credentials, leaving
the384MiB foreground role's two command workers and beat reserved for Study.
Small held-case source calls remain in foreground while optional roles are
paused. No allocation, service, lifecycle, admission policy or deadline changes.
