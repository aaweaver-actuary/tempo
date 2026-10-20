# Legacy bridge: scoped capture and per-card integrity checkpoint

Continuation began 2026-10-18 19:28:58 UTC; checkpoint target 20:28–20:58 UTC.
This supersedes capture/readiness limitations in the [receiver checkpoint](legacy-game-study-receiver-checkpoint.md)
where described below. Inputs and tests are synthetic; no live inspection, scan,
task execution, export, queue delivery, review, deployment or image build occurred.

## Bounded complete capture

`legacy_game_capture.capture_source_scope(CaptureRequest(...), reader, metadata)`
is a read-only owner port. It needs a repertoire, one explicit canonical game and
bounded decision plies. The existing worker prepare hook adapts its immutable v3
delivery to that port; inspection does not construct or accept an obligation.

Preflight records exact counts and highwaters for all five source inventories.
Each read returns at most 64 rows and closes before replay/hashing. PostgreSQL's
own row comparisons/collation control seek advancement. Fixed owner limits are
32,768 total rows, 520 pages including EOF probes, 20 seconds, 32 MiB read bytes
and 64 KiB per row. The entire preflight must fit before traversal begins. Source,
catalogue, graph and integrity metadata must remain identical across categories
and at EOF. Deadline, incomplete exhaustion, malformed/unknown-color source,
nonadvancement or any overflow yields unsupported/stale, never partial authority.
There is no automatic cap increase, permanent route index or second work frontier.

The complete global authored catalogue is traversed even when most rows are
irrelevant to the selected game. Retention is limited to 64 encounter candidates,
256 relevant graph rows, 128 physical cards and 100 memberships per retained card.
Complete prefix policy context includes routes that ended or diverged before the
decision; its 10,000-entry bound is enforced while accumulating. Previously
diverged routes cannot reenter through a later matching FEN. Every foreign,
unbound or unsupported competing candidate survives the filter. Physical graph
rows are retained only when their exact executable can cover a scoped decision;
their complete stream is still digested. Uncertain bindings fail closed.

Reusable authority uses a versioned ordered row-stream digest of complete authored
source and graph data. It excludes game identity, scope, candidate subset, capture
time, scan time and mutable card eligibility. Physical ID/revision/full executable
remain explicit in accepted facts. Physical/membership stream digests are retained
as evidence, while exact candidate facts and availability are fenced separately.
Two games on the same physical card therefore share one current authority key.

## Explicit scoped proof and shared comparison

Legacy-local snapshot schema2 has `catalogue_scope`; its retained `catalogue` is
never labelled globally complete. The proof records all stream EOF manifests,
reusable authority, current integrity certificate, canonical game/time/moves,
decision scope, retained candidate IDs and complete prefix policy context.
The backend independently reconstructs it; a caller's self-reported EOF grants
no acceptance. The existing v3 wire is unchanged. Trusted owner proof is retained
with the shared contribution, separately from the producer's capture digest.

A bridge reuses its original full physical executable/interior projection and
supplies policy anchors from complete prefix context. Game Evidence normalizes
the new local variant into its existing route-aware comparison and projection.
Comparisons outside the explicit scope, other games/times, uncertain introduction
chronology or owner-blocked cards do not become applicable physical misses.
Existing activated-publication v1/v2 and legacy-local schema1 behavior remain.

## Current completed integrity, exact physical card

`postgres_integrity.current_integrity_certificate` is a read-only projection of
the existing scan owner. It requires completed current graph and scan tasks,
matching run/graph generations, idle/error-free scan, complete source progress
and a known checked timestamp. `clean` and `needs_repair` can qualify; unchecked,
incomplete or old scans cannot. In particular, retained graph1 ready with a newer
graph2 queued is not current readiness. No historical scan is relabelled current.

An exact full physical card needs pending_validation clear and no owner-published
block, including a conflict at another decision in its executable. Acceptance
locks all relevant physical candidate IDs in sorted order before remediation,
rechecks full card/membership/deletion/block facts and introduction chronology,
and fences the current integrity certificate. An inactive competitor becoming
eligible after preparation is stale, even if the target itself did not change.
The existing shared graph reservation also fences new rebuild requests.

No maintenance execution is added. Existing scan publication caps of 32 issues
and 64 card-source blocks stay unchanged. Its segmentation/normal queue refresh
side effects cannot be described as target-only. The actual main's queued newer
generation remains a separate readiness prerequisite owned by the parent.

## Narrow shipping boundary and qualification

This is a reviewable foundation, not yet a user-facing Study bridge. The smallest
useful shipping slice remains one manually selected completed game mapping to one
existing introduced card: durable acceptance and truthful status, normal prelocked
queue delivery, atomic first-issuance frontier binding, and one genuine qualifying
normal Study review with exactly-once settlement. HTTP→Celery transport/status and
the actual queue/review/reconciliation/invalidation adapters are still required.
Do not expose acceptance that suppresses the legacy path before its replacement
delivery/settlement path is ready. Broad sync, new cards, revision>1, general UI,
curriculum expansion and maintenance behavior remain outside this checkpoint.

Focused source tests cover a foreign/unbound/revision2 competitor after row4,096,
prior-prefix ambiguity, no FEN reentry, two games/one authority key, full-card
blocks, unrelated needs_repair, current/queued generation refusal, immutable
inspection and acceptance races. Exact outputs and head are in task evidence.
Installed artifact/native behavior, image packaging, backend migration/command
durability and actual Study remain unqualified. No heavy window has run.

The [finite runner plan](legacy-game-study-qualification.md) fixes 251 artifact
cases and five module-native cases, with hard helper caps, a 5-minute cleanup
reserve and managed resource ownership. Those tests do not establish full v1/v2
wire exchange, backend HTTP/migration or genuine Study behavior.
