# Launcher transition preparation

**PROPOSED SOURCE / SYNTHETIC FIXTURES / DEPLOYMENT UNVERIFIED.**
This packet implements the lifecycle portion of the accepted
[launcher cutover specification](../../../../docs/architecture/study-first-reduction-2026-10-21/LAUNCHER-CUTOVER.md)
under #359. It does not change the current normal CLI, runtime, Compose files,
installation registration or Study services. The driver currently accepts only
`executionScope: "synthetic-fixture"`; production execution reports
`accepted-transfer-contract-pending` before acquiring a lock or changing files.
There is no production transfer/publication adapter or accepted receipt schema.

The [transition driver](../../../tempo-cutover-transition.mjs) reuses public
`acquireTargetLock` and `atomicJson` from
[the existing deployment component](../../../tempo-deployment.mjs). It keeps the
original target's state directory and lock identity throughout the transition,
even when the proposed target has different volumes and a different target key.
The composition root must supply these paths explicitly; the module never
derives a new state destination from a matching volume name.

## Implemented ownership and sequence

```mermaid
sequenceDiagram
    participant C as Existing launcher composition (unwired)
    participant L as Transition lifecycle
    participant S as Existing source lock and private journal
    participant P as Injected semantic ports (fixture only)
    C->>L: Pinned intent, original identity, explicit state paths
    L->>S: Acquire existing maintenance.lock
    L->>P: Fresh plan/checkpoint/lock-context gate
    L->>S: Durably preserve exact original bytes and digests
    L->>P: Fence legacy ingress/writers
    L->>P: Final source snapshot and restore verification
    L->>P: Import exact fenced source and verify semantics
    L->>P: Quarantine and restore-verified target backup
    L->>P: Finalize pair from accepted external transfer receipt
    Note over L,P: Same pinned artifacts, target, workspace and Compose identities
    L->>P: Fresh publication release gate
    L->>S: Publish matching pair and confirm file/directory fsync
    L->>P: Fresh activation release gate
    L->>S: Persist activation-started
    L->>P: Activate selected finalized target and verify
    L->>S: Persist completion; release original lock
```

Lifecycle owns phase and publication state. Injected ports own their service,
backup, history, authority and verification effects; their absence blocks the
dependent transition. No converter module, sibling plugin implementation,
Learning queue endpoint, Games service, optional worker or second launcher is
imported. No database operations or product decisions move into this module.

The initial intent has `version`, `transitionId`, `revision`, `sourceIdentity`,
`targetIdentity`, `lockIdentity: sourceIdentity`, `registration`, `receipt`, and
`original: {registration, receipt}`. Registration/receipt contain explicit
`cutover` identity templates. They do not require a future fenced transfer
receipt before fencing. The injected `finalizePublication` runs after the final
import and returns `{registration, receipt, transferAcceptance}`; it may change
only the opaque `cutover.transferReceipt` reference in the selected pair.
The public structural planner can be wired here by the composition root.

`assertReleaseGates(plan, checkpoint, facts, gateContext)` must return
`{status: "ready", evidenceId, binding}`. Binding is
`{version: 1, checkpoint, gateContext, planSha256}`; `planSha256` hashes canonical
JSON of the complete selected plan (object keys recursively sorted, array order
preserved, JSON scalars only). `gateContext` is a fresh nonce generated while
holding the existing lock. This rejects stale/miswired verdicts; it does not
replace actual authority observation. Gate inputs after finalization contain
the finalized selected records. Before activation, lifecycle adds publication
facts only after exact readback and successful file plus parent-directory fsync.

The readiness adapter owns `before-fence`, `before-resume`,
`before-publication`, `before-activation`, and `before-forward-recovery`
checkpoint decisions. Forward recovery needs the finalized transfer/publication
pair and a fresh fence, and never implies zero accepted effects. Original
restoration instead has its own `proveOriginalRestoration` capability; its
`before-original-restoration` binding is produced with lifecycle's exported
`cutoverReleaseBinding`, not the readiness evaluator. These capabilities are
separate because forward repair and proof of zero target-only effects authorize
different operations.

On a resumed unfinished transition, a fresh gate and fresh original-authority
fence/pending-work evidence are required. Interrupted effects must be reconciled
with their existing operation receipts. Unknown completion blocks; the lifecycle
does not blindly rerun a possibly accepted operation. The semantic operation key
is the pinned transition plus its named phase.

## Recovery boundary

| State | Behavior |
| --- | --- |
| Interrupted preparatory effect | Reacquire the original lock, verify fresh fence/pending authority, and recover the original effect receipt or positively prove it never started. |
| Only one publication record persisted | Block both writers. No inferred authority, automatic repair or fallback. |
| Pair persisted but completion journal write interrupted | Require exact pair match, fresh gates/fence and fresh durability confirmation before proceeding. |
| `activation-started`, uncertain acceptance or later accepted effects | No automatic legacy fallback. Explicit forward recovery uses the finalized selected target. |
| Explicit original restoration | Require fresh nonce-bound evidence of zero target-only effects, both sides fenced, original source untouched and original browser work recoverable. Restore the exact prior bytes and prior absence; keep writers fenced. |

Restoration does not restore a database, restart legacy services or promote a
backup. Target-only effects include durable intents, receipts, issuance, queue
changes and maintenance, as specified by the accepted cutover contract. A crash,
timeout or failed HTTP response never proves zero acceptance. A later accepted
live adapter must supply genuine evidence; fixture objects prove only plumbing.

The private journal includes original bytes and external evidence references.
It uses private file mode `0600`, file fsync and containing-directory fsync;
durability failures propagate and block activation. Never publish journals,
private observed records, credentials or snapshots to GitHub. Original byte
digests and parsed record identities are checked before resume or restoration.
The module preserves record bytes and absence, not historical filesystem owner
or timestamps. Restoration writes private records with mode `0600`.

## Focused verification

Run `node --test scripts/tests/tempo-cutover-transition.test.mjs`. Tests use only
task-owned `mkdtemp` files and fake semantic capabilities; every temporary
directory is removed by the test cleanup hook. They exercise the existing
maintenance lock, durable intent ordering, interrupted effects, final-source
binding, finalization identity protection, split publication, activation fences,
explicit forward recovery, exact original-byte/absence restoration, stale gate
rejection, corrupt recovery evidence and blocked production scope.

These checks do not repeat the six completed artifact/qualification suites and
do not establish PostgreSQL, Docker, browser, real-history or live-cutover
acceptance. Actual #358 authority, #267/#389 latest-state history and pending-work
preservation, a frozen public accepted-transfer contract, independent acceptance
and user cutover approval remain required external gates.
