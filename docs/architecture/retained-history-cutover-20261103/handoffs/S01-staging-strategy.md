# S01 — Final-fenced staging contract

Design baseline: `967289c3e9463b5bf6bdd631bb30d18c46df19c4` (origin/main checked
2026-11-04). Design-only supplement to [contract v1](../contract-v1.md) and
[recovery protocol](../migration-state-machine.md), under #908 DF-02–05.
Delivery owner: programme coordinator; persistence owner: Learning database.
Stop condition: freeze the strategy, owner boundaries and finite proof below.
No capture, migration, schema edit, service operation or activation is authorized.

## Decision: rebuild a fresh final target

Choose A: allocate a separately identified, wholly task-owned, non-serving final
Learning database; apply the pinned owner migration map and rebuild from the
final fenced source capture. Warm work is optional disposable rehearsal only.
Never reuse its position/fact tables for changed final input, even with a new
transfer ID, batch ID or generation string. Keep warm evidence; reclaim only the
verified disposable inactive allocation under existing cleanup authority. Never
truncate historical installed tables. A final database is not made safe by being
named “staging”: prove ownership, isolation, no serving registration and no
accepted target writes before any cleanup or rebuild.

Reject B (generation-scoped keys plus delta reconciliation): it changes every
identity/reference/receipt boundary and needs deletion, source-edit and accepted
operation delta semantics not presently implemented. No measured necessity
justifies that complexity. P0C's capacity.json provides synthetic 10,000-definition
model measurements, not full retained-state transfer duration or real graph
capacity. A is the design default; actual size/window/capacity remains a live
release BLOCK until representative rehearsal. Insufficient capacity means stop
and revisit with evidence, not silently select B.

The existing 0004 primary position key and global source/target fact keys make
this choice concrete. `stage_position` rejects any already-staged physical
position, regardless of changed receipt generation. Neither 0004 nor 0005 is
edited. No S01 schema migration is required. T02 exclusively owns reserved 0006
and staging adapter expansion. S02 exclusively owns reserved 0007, a separate
synthetic non-serving position effect owner; no automatic adoption of staged
facts into that owner is implied. Coordinator serializes manifest changes.

## Frozen bindings and transaction rules

1. Keep the programme transfer ID stable across warm/final phases. Give the fresh
   final allocation a distinct target authority and generation, bound to its
   physical database/workspace and pinned schema-map identity in owner evidence.
   Phase operation IDs are stable `(transfer_id, phase)`; batch IDs/cursors are
   deterministic within the final manifest. A new candidate allocation requires
   an explicit journaled replacement binding; never silently retarget receipts.
2. Fence source issuance, review writes, accepted-operation writers and new
   producer admission at an enforceable owner boundary; drain or reject existing
   writer transactions before declaring the fence complete. This denial point is
   the linearization boundary. Preserve fence/lock ownership until activation or
   verified pre-acceptance abort. Stopping a process is insufficient (F13).
3. After denial, capture a transactionally consistent PostgreSQL snapshot with
   the owner-defined watermark, schema/authority/epochs and complete source store
   inventory. A timestamp, max ID, or WAL location alone is not completeness.
   Export every required family including explicit empties, accepted unsettled
   operations, receipts, issued attempts and checkpoints. An operation committed
   before the fence is included with its original identity; a transaction denied
   before commit is not labeled accepted. Ambiguous/in-flight outcomes BLOCK.
   Other authorities (Content and browsers) retain their own evidence and
   original-key recovery; PostgreSQL cannot certify unseen device journals.
4. Use existing frozen `SourceManifest` fields: `capture=final_fenced`, `fence_id`,
   `source_watermark`, distinct `target_authority`, schema digests, all eight
   family inventories/evidence, store PK shapes/counts/digests and original facts.
   Final input is immutable. Changed source digest invalidates warm verification;
   final staging uses new final batch identities, not changed input under a warm
   receipt. A changed final capture needs explicit candidate replacement/replan.
5. Existing `StageReceipt` binds transfer/batch, manifest/batch digests, generation,
   cursor and original receipt ID. Each owned transaction commits data + receipt
   + checkpoint atomically. Same bound input returns the original receipt;
   changed input under the same identity conflicts. Per-batch retry is bounded
   and uses the same logical operation. The representative adapter currently
   imports exactly one state and is not a complete multi-batch exporter/importer.
6. Preserve original PKs and references. Distinct routes to the same exact
   repertoire/variant/position do not create duplicate position schedules. Exact
   duplicate physical position in another new batch is a conflict, not upsert or
   extra credit. Any grouped mapping needs reviewed lossless provenance first.
   Preserve full serialized FSRS TEXT, internal ID, due, donor, versions, study
   timezone and scheduler settings. Check all source/target/future creator ID
   namespaces (including reserved position range) before import; collision BLOCKS
   unless a separately reviewed bijection covers every reference. No reseeding.
7. Required held/cold/uncertain state remains identified with its actual reason.
   The representative introduced-state adapter cannot encode all seed holds;
   do not coerce these into introduced state. Required unsupported state BLOCKS.
   ARCHIVE only demonstrably non-serving/replay/debt/recovery evidence, with
   immutable readback; never use archive to omit accepted obligations.
8. Final verifier independently reads the final target, checks every original
   key/byte digest/reference, required dispositions, full graph/alias coverage,
   FSRS namespace and semantic fixtures against the final manifest. Counts alone
   are insufficient. Bind `VerificationReceipt` to final manifest + generation;
   any required hold without preserved supported handling is BLOCK. Only the
   Learning activation owner may consume the matching receipt and `FencingProof`
   under the retained lock. Content and Learning commits are not one transaction.

No new wire DTO is introduced by this memo. The existing DTOs cannot themselves
prove physical allocation, inventory completeness or an actual fence. Their
`verifier_evidence`, fence evidence and operation journal must bind those owner
facts; adapters for complete capture/verification/activation remain missing.
Do not mistake claimed strings for authenticated native receipts.

## Six operations and existing ownership

| DF-02 operation | Existing owner/surface and precise remaining boundary |
| --- | --- |
| capture_snapshot | Each storage owner; Learning source fixture `database/tests/position_study/test_retained_position_store_postgres.py::capture` demonstrates selected schema-59 rows only. Complete production capture is absent; Content owns graph export, browser owns original journals. |
| plan_transfer | Pure `tempo_learning_transfer_contracts.v1.plan_transfer`; actual family mapping/completeness evidence remains each owner’s responsibility. |
| stage_batch | `database/tempo_learning_database/adapters/retained_position_store.py::stage_position`, caller-owned transaction; T02 owns bounded expansion. No source SQL or scheduler in the application coordinator. |
| verify_staging | Learning database verifier must independently read staged rows; `VerificationReceipt` and pure `activation_blockers` are contracts only, not a native verifier. |
| activate | Single Learning authority owner, retained lock/fence, atomic effect + activation receipt. Not implemented for retained staging; existing fresh-start transition/launcher must not be reused as retained-history approval. |
| verify_runtime | Existing `database/tempo_learning_database/runtime_readback.py::verify_runtime` supplies limited installed schema/workspace/role readback. Delivery owner adds actual native/browser one-writer, save→receipt→playable successor, Tactics and recovery proof; current readback alone does not establish these. |

## F14 disposition table

| Interruption | Deterministic disposition |
| --- | --- |
| Before fence | Old writer sole authority. Warm candidate remains non-serving; resume same input or allocate final candidate after eventual fence. No stale verification activation. |
| During final stage | Retain fence; atomic batch either absent or original receipt present. Read receipt and resume same immutable batch; changed digest conflicts. If abandoning, first prove target never accepted and denied before verified old-writer return. |
| After final verify, before activation | Re-read fence, final manifest, allocation and receipt bindings. Any mismatch BLOCKS; no warm receipt substitution. Resume same activation operation only while source remains fenced. |
| Activation outcome uncertain | Keep old writer fenced. Read authoritative activation/effect receipts under original ID; absence of ACK is not absence of commit. Forward repair until outcome established; never automatically restore stale DB/image. |
| After activation / target review | Target remains sole writer; preserve new review, effect, receipt and checkpoint. Forward recovery/replay once. Returning to old DB requires separately proven lossless reverse migration and authority; backup existence is insufficient. |

## Finite proof handoff (not executed by S01)

Reuse the owned Learning PostgreSQL runner and
`database/tests/position_study/test_retained_position_store_postgres.py`;
its existing `test_f01_retained_position_atomic_exact_replay_and_negative_cases`
already covers changed-digest batch conflict, transaction rollback and namespace
collision. Those historical facts are source inspection, not a new PASS.
Coordinator/T02 should add named case
`test_warm_changed_final_requires_fresh_nonserving_target` in that same suite:

1. Use its existing `populate`, `capture`, `TARGET_DDL` and `stage_position` in the
   runner-created disposable database; warm-stage and commit original manifest
   into schema `warm`. Save all state/fact/batch rows for equality comparison.
2. Reuse the existing test's altered `source_digest` fact construction, original
   bytes/digest and recomputed store inventory. Validate the new manifest. Same
   transfer/batch with changed digests must raise `immutable batch conflict` and
   leave all warm rows unchanged after rollback.
3. With the same altered valid manifest and a distinct batch ID/receipt ID and
   final generation, the warm schema must raise `immutable position identity
   conflict`, proving generation labels do not namespace physical state. Assert
   no extra batch/fact rows committed. Changing transfer ID is equally unable to
   evade the position PK; if tested, rebind manifest and receipt together.
4. Apply unchanged TARGET_DDL to a second empty task-owned schema `final`, stage
   the changed input, commit, replay it, and assert exact changed bytes/state,
   one state, one batch and unchanged warm rows/source. Schemas are a bounded
   storage-mechanism test only; actual release policy requires a fresh database.
   Do not claim a synthetic manifest constitutes an enforceable source fence.

Other required negatives: final verification rejects warm/final watermark or
allocation mismatch (reuse contract suite); missing accepted-operation/store
inventory remains BLOCK; crash after authoritative target commit cannot select
old-writer rollback (reuse `recovery_path` contract, then native F14 owner proof).

One native candidate rehearsal, owned by delivery coordinator after missing
adapters exist: fully isolated source + warm/final databases; accepted source
operation after warm stage; enforceable drain/fence; final consistent capture;
fresh final import; interruption before/after a batch commit; complete digest and
identity comparison; activation receipt readback with lost ACK; one new target
review and exact-once replay; rejected stale writer and no stale rollback.
Record exact SHA/schema/runtime/allocation and cleanup receipts. This is not
executable full-source migration today. Real size, backup restore, privilege and
credential topology, supported installation and late-device recovery remain
release BLOCKS, not reasons to expand this pure design.

S01 validation: documentation diff/links/whitespace only. Runtime architecture
impact: none implemented; existing canonical staging diagram still describes
non-serving copy with no activation edge. Native/browser/assembled/installed/
activated: NOT RUN. Programme remains NO-GO; coordinator owns integration.
