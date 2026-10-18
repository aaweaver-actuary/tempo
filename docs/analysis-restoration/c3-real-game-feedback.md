# Real-game feedback rehearsal

This uses the existing A operator, Game Evidence, external C pilot and Workbench.
Private captures, game manifests, PGNs, credentials and results stay outside Git.
It is a disposable rehearsal, not live migration or recurring synchronization.

## Authority and identity

Verify the authorized read-only capture checksum and published graph generation.
`tempo-a-legacy-export --capture CAPTURE --graphs GRAPHS --workspace WORKSPACE
--source-key SOURCE --repertoire-id ID --output DIRECTORY` translates a complete,
explicit repertoire scope into the existing A documents. Repeat `--repertoire-id`
for multiple repertoires. Inputs are explicit operator paths/IDs, never inferred
from opening names. Repeating output requires byte-identical results.

Inspect `identity-mapping.json`. Exact compatible aliases preserve historical
physical identity. Normalizing an opponent prelude creates a new executable;
`historical_mapping_unknown` suppresses confirmed learner misses for these
unresolved identities. Ordered route anchors use the full captured repertoire
catalogue to avoid merging learner policies. No FEN-only re-entry is authorized.
This adapter does not complete legacy history adoption.

For a compatible installed Content/Learning/Game Evidence assembly, the optional
`--preserve-physical-execution` translation keeps an original opponent prelude
inside the physical executable. Explicit logical learner roots and uniquely
attributable interior offsets are validated by each authority. Full original
FEN/line/revision/color alias checks are retained; ambiguous interior ownership
remains unknown. This is an opt-in input shape, not a live history migration.
Use fresh disposable rehearsal storage when replacing a provisional normalized
source whose identities were already accepted. Never rewrite those old aliases.

Use Content `import plan SOURCE --manifest MANIFEST --expected-revision REVISION`,
inspect the exact durable draft, and explicitly approve for disposable rehearsal.
Apply through the installed A bridge with authenticated Learning. Retain operation
IDs across response loss. A terminal rejection needs a fresh inspected approval.
Export `snapshot OP --output FILE` only after actual activation and load it with
Game Evidence. Never fabricate activation or historical aliases.

Bounds: 8192 complete definitions, 16 MiB authored input, 12 MiB Learning bundle,
64 MiB immutable Content export, 128 MiB enclosing comparison snapshot. Scope is
never truncated. Internal checkpoints use bounded hash-verified compression.
Public canonical contents and old inline storage remain readable. Learning's
transaction timeout is unchanged; integrity membership lookup is now linear.

Provision enough PostgreSQL shared lock capacity for physical and Content IDs
together. The actual 6,524-alias rehearsal found `out of shared memory` at
`max_locks_per_transaction=64` / `max_connections=48`; stage acceptance remained
durable and activation stayed queued. The owned qualification profile uses
startup setting `max_locks_per_transaction=512` and records the actual settings.
This is a task-local database provisioning choice, not a runtime change or live
migration. Keep the same durable intent when retrying after verified provisioning;
do not remove card-edit locks or raise transaction timeouts to hide the failure.

The Learning-owned disposable restore helper advances review identity allocation
after copying explicit historical IDs. For an earlier unfinished rehearsal,
`restore_rehearsal.py --repair-review-identity` uses the same explicit
configuration/capture/manifest/output arguments. It verifies owned-container
labels, endpoint, workspace and exact unchanged copied review contents before
advancing the sequence; it never lowers an existing sequence or changes history.
Do this before any new scripted rehearsal review. It is not a live migration.

## Selected games and analysis

`python -m tempo_analysis_pilot.cohort --accounts ACCOUNTS --capture CAPTURE
--since AWARE_TIMESTAMP --until AWARE_TIMESTAMP --output DIRECTORY` freezes the
selection before findings. ACCOUNTS is an explicit provider/account JSON mapping.
The bounded window is at most 366 days. Selection: 60 recent rated rapid/classical
by provider/color round robin, then up to 20 underrepresented
provider/color/captured-opening strata; blitz fills remaining supply. Captured
source records and reconstructed PGN bytes have separate digests. Original
provider PGN bytes and unverified completion times remain unknown.

Import selected PGNs through Game Evidence's public CLI and collect IDs from
receipts. Run bounded comparisons, inspect all selected reports, then create the
immutable export with every exact `--game-id`. Verify offline and replay without
changing bytes. Do not silently replace this with an account-wide selection.

Run the [existing pilot](../../tools/analysis-restoration/c_pilot/README.md) with
the export, exact IDs, installed Intelligence command, stable run ID and bounded
budget. Every producer page is verified before projection to actual encounters.
Contradictory route scopes remain unknown. Optional Stockfish must use its pinned
qualified client/worker and hard cgroup. Deferred work and insufficient depth
remain explicit; a source-backed partial report is not complete C3 acceptance.

## Workbench and Content improvement

Build and serve the independent Workbench on an allowlisted origin. Connect to
the exact Intelligence workspace with its caller credential. Report reads do not
start import/engine work. Inspect the board, actual/expected moves, provider link,
policy, supporting games, denominator or explicit unknown, provenance and current
disposition. Existing durable browser command recovery remains authoritative.

Context exports are informational. Proposals require Content inspection, a draft,
exact approval and genuine Learning activation. Rehearsal approval is not user
approval of a live edit. Verify retained histories and immutable older reports,
then compare against a new activated snapshot. Learning alone admits new content.

## Acceptance

Retain private source identities, exact commands/executables, mapping diagnostics,
per-game/manual finding audits, report hashes, real Chromium screenshots,
activation/replay/recovery and ownership/cleanup receipts. Report C3-A through
C3-G separately. Missing policies, unresolved history, provider limitations and
engine backlog must constrain the verdict. Native synthetic fixtures alone do
not establish real-game feedback.

An uncovered route that reaches existing same-policy authored content remains
unknown until route/re-entry authority is verified. Existing Content definition
IDs are retained in the source observation; a new training card is not inferred.
The corrected export uses derivation3 for new input identities while retaining
`tempo-analysis-inputs` schema v1. Reusing an existing reserved output directory
still returns its original frozen bytes. Choose a new directory to obtain the
corrected derivation; do not overwrite historical evidence. Pages contain at most
64 items and 8 MiB including the persisted newline; the full page set remains
bounded at 64 MiB. Dense observations split across additional pages without
truncation. Legal inventories are limited to opponent positions encountered in
the exact selected games, preserving every scope and reply at each retained
position. Complete definitions/routes and all selected game observations remain
in the export. The repeatable-read source cut closes before chess traversal and
serialization; source delivery receipts remain part of immutable input identity.

The Workbench's selected-game browser uses the advertised `selected-games-v1`
capability and pages through Intelligence's accepted input, including games with
no positive finding. It replays supplied legal moves for display and shows the
original comparison context; it does not infer repertoire authority in the
browser. A different run/input revision is rejected before board rendering.
