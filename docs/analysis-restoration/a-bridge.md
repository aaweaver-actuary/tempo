# A: authoritative repertoire bridge

Status: interface checkpoint; implementation and consumer integration pending.

Branch: `codex/analysis-a-bridge`. Base: `0edf566ad48f51846a43d3341809379ff0469ada`.
Checkout: `/Users/andy/.codex/worktrees/analysis-a-bridge/tempo`.

## Ownership

A owns Content import/draft/approval/export, Learning publication v2 and historical
identity aliases, Game Evidence snapshot v2 and analysis export, and external
`tools/analysis-restoration/a_bridge/` integration tooling. Existing shared Content
Publication v1 and Game Evidence delivery v1/v2 remain unchanged. B owns engines;
C owns Intelligence, findings, Workbench, and final pilot assembly. A alone owns
the relevant component registration and append-only migrations in this change.

## Proposed commands and transport

Existing: `tempo-content source load/show`, `inventory request/work/status`,
`publication build/show`; Learning `POST /internal/publications/stage`,
`activate`, `retire`; Game Evidence `load-snapshot`, `import-pgn`, `work`, `report`.

New: `tempo-content import plan INPUT --manifest FILE --expected-revision N`,
`proposal inspect FILE`, `draft create --proposal FILE`, `draft show ID`,
`draft approve ID --digest SHA256`, `operation show/cancel ID`, and
`publication export ID --generation N`.

New external command: `tempo-a-bridge apply OPERATION`, `reconcile OPERATION`,
`snapshot OPERATION --output FILE`. These invoke installed CLIs and authenticated
Learning HTTP; no cross-domain SQL or service implementation imports.

New Learning operator endpoints: `POST /internal/publications/v2/stage`,
`POST /internal/publications/v2/activate`,
`GET /internal/publications/operations/{id}`,
`POST /internal/publications/operations/{id}/retry`, and
`GET /internal/publications/{id}/status`. The operator credential is distinct
from Study and evidence credentials. No credential enters a payload/export.

New Game Evidence commands: `analysis-export create --workspace W --output DIR`
and `analysis-export page ID --workspace W --kind KIND --after CURSOR`.

## Versioned local documents

These are service-local schemas, not new shared v1 contracts. Unknown fields and
unsupported versions fail explicitly. Existing v1 input readers remain available.

* Authored import manifest v1: explicit workspace/source/repertoire/policy/color,
  stable entity mappings, complete declared scope, source hashes/provenance and
  optional one-to-one historical aliases. Bare PGN is insufficient authority.
* Learning bundle v2: unchanged shared Publication plus opening execution facts,
  actual route occurrences, source digest and validated aliases. Target capacity
  is a complete 2,048-definition source, subject to native measurement.
* Comparison snapshot v2: immutable Publication, exact source export, real
  ActivationAcknowledgement and separately observed current-serving status.
* Analysis inputs v1: `kind: "tempo-analysis-inputs"`, workspace, immutable
  `input_id`, revision, manifest digest, source/publication/snapshot identities,
  provenance, completeness and hashed serialized pages. Page kinds are
  `definitions`, `routes`, `legal_replies`, `games`, `positions`, `comparisons`,
  and `deviations`. Pages contain at most 64 items. C retains all lineage fields.
* Response proposal v1: `kind: "response_proposal"`, `status: "draft"`, proposal
  identity, workspace/source/repertoire/policy/color, expected source revision and
  digest, exact route/ply/FEN anchor, opponent deviation, legal response lines and
  provenance/evidence references. `gap_context` is informational and inapplicable.

For each opponent deviation the export includes the established pre-deviation
route/policy/color, actual opponent move, resulting learner game/revision/ply/FEN
and move history, selected complete response inventory/revision/digest, and
explicit re-entry evidence. Missing, ambiguous, unavailable or unactivated
coverage stays unknown. Original comparison statuses/reason codes are preserved.

Manifest cursors bind input/workspace/kind/revision/digest; consumers must not mix
pages. Exports remain usable while producer services are stopped. Historical
activation proves only activation at revision R. Serving status records a separate
authenticated observation; offline data never authorizes current Learning effects.

## Safe defaults and examples

Synthetic-first rehearsal; private real exports remain unverified. Re-entry is
false unless explicitly authorized. Approval reserves the entire source. Content
serializes cancellation against durable application authorization; after that
point cancellation returns `application_started` and the original operation must
reconcile. No automatic rollback, repertoire mutation, admission or fake reviews.

Minimal valid Content input remains `services/content/fixtures/source.json`.
An invalid input is that same document with `workspace_id` removed; validation
fails before source admission. `tools/analysis-restoration/a_bridge/fixtures/`
contains `response-proposal.json` (valid draft shape, synthetic placeholder digest;
not applicable to a current source) and `invalid-proposal.json` (informational
context, never executable). Its serialized document is:
`{"schema_version":1,"kind":"gap_context","status":"draft","proposal_id":"informational-only","accepted_lines":[]}`.
Actual analysis-export fixtures will be generated by the installed producer before
consumer integration; handwritten activated acknowledgements are not acceptance
evidence.

Approval binds the exact draft/source revision/digest/scope. Repeated delivery
reuses immutable operation IDs. HTTP 202 remains pending. Current receipt/status
reads precede exact replay after unknown outcomes. Empty successors use retirement.

Remaining scope: full importer platform (#265), general live-history migration
(#267), broad structural UI (#193), and final pilot assembly (#266). No live
installation, recurring imports, deployment, Actions or merge changes.
