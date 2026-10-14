# Tempo component contracts — wire v1

This is the executable interface foundation for [#264](https://github.com/aaweaver-actuary/tempo/issues/264), under [M02 / #251](https://github.com/aaweaver-actuary/tempo/issues/251) and the adopted [#192 architecture](https://github.com/aaweaver-actuary/tempo/issues/192). It defines target contracts and tests reference examples. It does **not** implement or certify the current production services.

## Public artifacts and catalog

All five distributions start at **1.0.0** and require an explicit integer `contract_version: 1` in wire envelopes. Pydantic 2.12.5 models are authoritative. CPython 3.12 and 3.14 are qualified; metadata permits `>=3.12,<3.15`. JSON schemas use draft 2020-12 and are included in wheels. `tempo.invalid` schema IDs are identifiers, not hosted endpoints.

| Root / distribution | Public import | Producer → consumers | Authority |
|---|---|---|---|
| `learning` / `tempo-learning-contracts` | `tempo_learning_contracts.v1` | Study Web/Workbench/Game Evidence requests → Learning; Learning responses → web clients and domain clients | Learning |
| `content-publication` / `tempo-content-publication-contracts` | `tempo_content_publication_contracts.v1` | Content → Learning; Learning acknowledgements → Content/Workbench | Content definitions; Learning acceptance/activation/fencing |
| `game-evidence` / `tempo-game-evidence-contracts` | `tempo_game_evidence_contracts.v1` | Game Evidence → Learning, Intelligence, Workbench; Learning acknowledgement → Game Evidence | Game Evidence observations; Learning obligations |
| `intelligence` / `tempo-intelligence-contracts` | `tempo_intelligence_contracts.v1` | Intelligence → Learning/Workbench | Advisory evidence only |
| `analysis-jobs` / `tempo-analysis-jobs-contracts` | `tempo_analysis_jobs_contracts.v1` | Content/Game Evidence/Intelligence → Stockfish or Maia; worker → requesting domain | Requesting domain inputs; worker execution/provenance |

Analysis jobs additionally exposes public `stockfish` and `maia` modules. Each `v1` namespace exposes `PUBLIC_MODELS`, `validate_contract(model, json)` and `serialize_contract(model)`. The [machine-readable catalog](catalog.json) lists every top-level interface, schema export and owning suite. There is no shared ORM, global registry, provider initialization or universal model package. Small wire primitives are intentionally package-local to avoid a sixth runtime dependency or coupled releases.

## Learning interfaces and settlement

- `AttemptRequest` → `AttemptIssuance`: Learning issues a concrete card/exercise/publication/queue revision, not permission to execute any future definition.
- `ReviewSubmission` → `ReviewSettlement`: submission identity, issued attempt, answer, publication and queue revisions are explicit. Caller-reported outcome/guidance is input; Learning remains responsible for policy validation and authoritative settlement. These contracts do not implement grading.
- `ReceiptQuery` → `ReviewSettlement`: recover the original submission's recorded outcome after lost responses. No invented success when an operation is unknown.
- `QueueRequest` → `QueueIssuance`: capacity is requested; current eligibility, quota, deduplication and admission are exclusively Learning-owned. Issued attempts carry authority; empty, pending and retryable responses carry no issued attempt.
- `LearningObligation` and `LearningState`: observed evidence requests an obligation, not queue insertion or an FSRS update. Learning exposes authoritative revisions, accepted generations and pending obligations.

`received` promises only receipt of a request. `accepted_pending` identifies accepted work but has no committed review receipt. Only `committed` includes a receipt binding submission, attempt, card, review, required evidence, next due date, Learning revision and queue revision. Retryable and terminal failures, conflicts and stale attempt/version outcomes are separate variants; they cannot carry a receipt.

Learning owns one atomic review/history/schedule/required-evidence/receipt/queue transaction. No scheduling, eligibility, ordering or admission network service joins it. In-memory examples illustrate this invariant with bounded state; they are not persistence, crash-recovery or deployed concurrency proofs.

## Identity, revisions and replay

Opaque stable IDs are scoped by workspace and interface. Preserve card/decision/policy identity across revisions; issue a new identity for a different semantic entity. Source revision describes authored source, publication generation describes an immutable built generation, exercise revision describes executable structure, activation revision fences Learning's serving transition, and attempt/queue/Learning revisions fence their own authorities. These numbers are not interchangeable.

Operation keys are `(workspace_id, interface, request/submission/delivery/activation/retirement ID)`. Canonical equality means equality after validation and `serialize_contract`: sorted object keys, compact UTF-8 JSON, explicit defaults/nulls, finite numbers and interoperable integers. Arrays retain semantic order. Timestamp strings retain their supplied representation; changing an equivalent timestamp spelling changes the payload. Fingerprints/provenance identify inputs but do not replace payload-conflict checks or provide authentication.

Same key + same payload returns recorded state or the original committed receipt. Same key + different payload conflicts without effects. Retry lost responses with the original identity; query settlement if necessary. Accepted pending work may progress to committed, rejected or failed, but cannot fabricate completed effects. Retryable failure permits another delivery of the same intent, not creation of a new logical review. Terminal failure requires correction/new authorized intent. Receipt retention must cover every supported offline replay window; v1 does not promise a finite expiry. A future bounded retention policy needs an explicit compatibility contract.

At most one submission settles an issued attempt. Concurrent identical requests get one receipt; competing payloads or separate submission identities cannot both commit. Replay lookup precedes stale-version checks for already committed operations, so retirement cannot invalidate a historical receipt. Pending operations must recheck current authority before commitment.

## Publication and evidence lifecycle

Content publishes immutable executable definitions with stable identities, source revision and publication generation. Five exercise shapes are supported: move line, square set, knight path, choice and explanation. Definitions are independently represented at Content and Learning boundaries, with explicit consumer parity tests. Structural validation is not legal-position validation or proof a Content build executed.

`PublicationValidation` reports validated/rejected/retryable build results. `PublicationAcceptance` reports Learning's accepted-pending/rejected/retryable response. Neither activates content. `ActivationRequest` carries expected activation revision; `ActivationAcknowledgement` reports pending/activated/rejected/retryable/stale states for the exact activation identity and generation. An old acknowledgement cannot activate an older generation or overwrite a newer activation revision.

`RetirementRequest` may name a replacement identity/generation. Replacement must already be accepted and current. A completed `RetirementAcknowledgement` requires `stale_attempts_fenced=true`: Learning stops new issuance and rejects unsettled old attempts atomically with retirement/replacement. Pending retirement is not proof fencing completed. There is no grandfathering of uncommitted stale attempts in v1.

Imported games carry provider identity, source digest, game revision and observation provenance. Reimports preserve `(provider, provider_game_id)` and the internal game ID. Encounter identity preserves game/ply/decision identity; evidence revisions describe revised observations. Misses/comparisons refer to observed encounters and explicit repertoire generations. None are controlled study review events.

Game Evidence assigns stable obligation IDs for the same workspace/game/encounter/decision/kind across deliveries; delivery IDs may change without inventing a new obligation. Same evidence identity/revision or obligation identity with differing content conflicts. A changed observation revision needs explicit new acceptance; it does not silently rewrite a committed obligation. Learning returns accepted-pending/completed/retryable/terminal/conflict/stale acknowledgements. Only completion reports a committed Learning revision; repeated evidence must not duplicate obligations, queue effects or scheduling events.

## Advisory and worker boundaries

Intelligence publications bind content generation, Learning revision, policy, source/method versions, input provenance, production time and expiry. Probability `null` means unknown; `0.0` means known zero. Unknown mass is explicit. No expiry means freshness is unknown; conservative reference consumers fall back to deterministic ordering. Unavailable/incompatible/stale advice cannot manufacture eligibility, admission, FSRS updates or review events. Rankings remain subject to current eligibility, quota and card deduplication at Learning's commit boundary.

Workers share only identity, request/execution versions, time/memory constraints, lifecycle and cancellation metadata. Stockfish requests include FEN, moves, permitted search moves, depth/node bounds, MultiPV and engine version; results carry ranked variations and side-to-move centipawn/mate scores. Maia requests include FEN, rating, model identity/version and an explicit legal move inventory; results carry normalized probabilities with model/runtime provenance. FEN legality and engine execution belong to implementations, not these packages.

Job identity plus request revision binds immutable semantic inputs. Retrying execution preserves that request and advances execution generation; changed inputs require a new request revision or job identity. Pending/running/completed/failed/cancelled are explicit. Only failed executions can advertise retryability. A completed status is not sufficient to consume an unverified result. Match workspace/job/request/current execution generation/input digest and model/engine version before using a result. Worker results are observations, not permission to write Learning state.

Cancellation `requested` means delivery was acknowledged; `terminated` confirms computation stopped. `already_completed` does not imply cancellation succeeded. Stale cancellation acknowledgements and results cannot change a current execution. A result racing a cancellation request may be consumed only under the requesting owner's policy while termination remains unconfirmed; the reference permits a compatible result before confirmed termination and rejects it afterward. No worker receives unrestricted domain database credentials.

## Verification and limits

See [compatibility](COMPATIBILITY.md), [database ownership](OWNERSHIP.md), [regressions](REGRESSIONS.md) and [delivery evidence](VALIDATION.md).

JSON schemas validate wire shape, types, bounds and discriminated variants. Pydantic cross-field validators and independently maintained TypeScript semantic checks enforce additional coherence; schema validation alone is insufficient. Transition/replay examples add protocol checks. All are separate from current producer conformance and runtime enforcement.
