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

The recovery wrapper selects the existing PostgreSQL fixture's explicit
`study-recovery` profile: 1 GiB memory and one nonexternal, task-owned labelled
data volume. The default native profile remains 384 MiB with 512 MiB tmpfs.
The disk-backed profile keeps database/WAL storage out of the memory cap;
it does not raise any application transaction deadline. Allocation, exact
mount/label checks, memory evidence and container-before-volume cleanup are
retained in the fixture receipt. Build and run it under the existing resource
admission workflow after the coordinator verifies aggregate assembly capacity.

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
are checked. Every sample, first/cold sample and failure remains in the receipts.

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
