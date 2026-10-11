# Prefix transition structural admission

Related: [#81](https://github.com/aaweaver-actuary/tempo/issues/81),
[PR #187](https://github.com/aaweaver-actuary/tempo/pull/187).
This change depends on the reconciled PR187 recovery candidate and the journal
concurrency correction. The journal module owns validation, ownership and locks;
command modules own the final structural request boundary.

## Admission contract

`StructuralMutationScope` contains repertoire IDs, optional card IDs and
`scopeComplete`. `readPrefixStructuralAdmission` supplies advisory pause state and
an actionable reason. `admitPrefixStructuralMutation` rereads journal ownership
under the same named Web Lock as reservation/update/clear and calls `dispatch`
synchronously. The callback returns a wrapper around the request promise, so the
lock ends before network completion. Preparation, file hashing, status reads and
planning occur before acquiring it. Every later replay and retry is admitted again.

New command identities are allocated only inside admitted dispatch callbacks.
Outbox choices are checked before allocating a new identity and their final
requests are checked again. A saved earlier choice remains available for recovery
when later delivery is refused. Completed receipts can resolve without dispatch.
Transport uncertainty retains the original identity and existing backend policy.

Unavailable safe serialization, inaccessible/malformed storage, uncertain
ownership and incomplete overlapping scope refuse delivery. The target
`operation.repertoireId` always belongs to protected scope even when a summary is
corrupt. Matching planner evidence binds request, snapshots, depths and affected
scope; matching route IDs alone is insufficient. Historical journals are preserved;
missing evidence never grants permission for uncertain structural effects.

Explicit independent repertoire scope stays editable. Shared-card producers and
edits do not have authoritative complete membership inventories in the existing
client contract, so they supply `scopeComplete: false`. This intentionally pauses
them whenever an unresolved transition could overlap, rather than assuming the
visible repertoire owns every affected card. Card edits also supply card identity.

Open dialogs subscribe to admission changes, disable conflicting confirmations,
show the recovery reason and keep unsaved input. Displayed state is advisory:
a stale enabled button still reaches final admission. Cancel, source/preview/status
reads, recovery, study and inspection remain available. Verified resolution and
permitted journal clearing restore admission.

## Mutation inventory

The regular command regression file has one named
`issue81_<family>_final_admission_suppresses_request_and_new_identity` case for each
family below. Existing command files continue to prove idempotency and receipt
recovery. Outbox regressions additionally distinguish enqueue from delivery.

| Mutation / backend structural effects | Scope and final boundary | Named family / other coverage |
| --- | --- | --- |
| Repertoire deletion: source/cascade removal, primary card deletion or transfer, membership and graph removal | Target repertoire; planner scope includes every protected card owner/membership. Guard DELETE in `repertoire-delete-command`, including receipt-absent replay | `repertoire_deletion`; open deletion editor/browser cases; pending delete recovery |
| Canonical-prefix save: canonical route fields and graph request | Target repertoire; guard PUT in `canonical-prefix-command`. Preview POST remains a read-only planning command | `canonical_prefix_save`; stale open canonical confirmation; existing canonical recovery |
| Prefix depths: saved source depths and publication | Existing prefix transition reserve/deliver path; journal owner validates original approval and ownership, including replay and blocked retry | journal concurrency/command regressions; PostgreSQL prefix application driver |
| Branch creation: source and deterministic card/membership insertion, graph rebuild | Target + incomplete shared-card scope; guard POST in `branch-command` | `branch_creation`; branch pending regressions |
| Branch removal: matching source-line deletion and target graph/coverage invalidation | Target repertoire; guard POST in `branch-removal-command` | `branch_removal`; branch removal pending regressions |
| Card revision: revision/content, graph source dependencies and shared membership effects | Card ID, incomplete repertoire memberships; guard PUT in `card-revision-command` | `card_revision`; card revision pending regressions |
| Permanent card deletion: card purge and occurrence-aware graph dependency removal | Card ID, incomplete memberships; guard DELETE in `card-delete-command` | `card_deletion`; deletion preview/read and pending receipt tests |
| Prefix split acceptance: split/step creation and graph rebuild | Card ID, incomplete memberships; guard POST in `prefix-split-command` | `prefix_split_acceptance`; split pending regressions |
| Integrity repair: source/card repair and derived publication, operation/task retry | Target + incomplete shared scope; guard resolve and both retry deliveries in `integrity-repair-outbox` | conflicting enqueue and final-delivery suppression; regular repair receipt/task suite |
| PGN import: repertoire/source/card/membership ingestion | Incomplete unknown identities; guard import POST and blocked retry in `pgn-import-command`, including recovery redelivery | `pgn_import`; import lost-acknowledgment, same-file replay and blocked recovery suite |
| Analysis paste: selected repertoire source/card/membership insertion | Selected repertoire IDs + incomplete reused card scope; guard commit POST in `analysis-paste-command` | `analysis_paste`; pending paste suite |
| Finding card save and tactical preview save: deterministic card/membership creation | Incomplete memberships; guard save POST in `finding-card-command`; previews remain readable | `finding_card_save`; finding pending suite |
| Discoveries: admission of derived source/card/membership work | Incomplete identities; guard accept POST in `discovery-admission-outbox`; status polling remains readable | conflicting choice/retry and final delivery suppression; admission outbox suite |
| Opportunity Train: card/repertoire membership insertion | Target + incomplete reused card scope; guard train POST in `opportunity-command` | `opportunity_membership`; opportunity pending suite |
| Tactic capture: deterministic card insertion/reuse and membership | Incomplete reused identities; guard capture POST in `tactic-capture-command` | `tactic_capture`; capture receipt and editor suite |

## Exclusions established by backend behavior

Ordinary review/schedule and teaching-state updates do not change structural
card fields protected by migration038. They keep foreground study available.
Repertoire rename changes name only. Repertoire settings change scheduling/import
defaults, not existing source depths. Prefix split rejection records the rejected
suggestion; it creates no split. Opportunity dismiss/acknowledge/snooze and finding
curation update decisions/status, not card/source membership. Preview commands do
not save structural products. Endgame template edits have their separate template
identity. Import discard cancels an existing import; it does not create structure.
These remain governed by their existing idempotency/recovery contracts.

## Browser and PostgreSQL protection limits

This guard coordinates cooperating tabs in one origin with Web Locks and readable
browser storage. It supplies no cross-device pre-admission fence. A browser journal
reservation precedes server persistence; HTTP/broker 202 acknowledgment alone does
not establish a receipt, application record or structural fence.

After writer admission, PostgreSQL's existing barrier revalidates the approved
plan and atomically persists application state and repertoire/card fences. Triggers
protect structural card, source, depth, membership and graph writes. Ordinary
review/schedule updates are excluded. Publication recovery retains fences until
verified completion; rejected preactivation work follows the existing release
rules. Backend idempotency and these fences are preserved by this frontend change.

An approval erased before server persistence cannot be reconstructed. Total browser
journal loss after reload requires authoritative server discovery; the current
browser-only protocol does not provide it. #81 remains open. The separate
[durable-intent proposal](PREFIX-DURABLE-INTENT-PROPOSAL.md) describes that boundary.
