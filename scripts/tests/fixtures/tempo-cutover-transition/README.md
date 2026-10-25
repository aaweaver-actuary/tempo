# Graph-preserving fresh-start lifecycle

**SOURCE PROTOCOL PREPARED / SYNTHETIC PROOFS / DEPLOYMENT UNVERIFIED.**
The user selected a fresh start preserving the opening graph, authored sources
and repertoire structure. Old reviews, schedules, server/background work,
browser operations and old backup retention are outside this transition's
requirements. This supersedes the old-history transfer policy in the earlier
[cutover specification](../../../../docs/architecture/study-first-reduction-2026-10-21/LAUNCHER-CUTOVER.md)
for this bounded source preparation. Previously authored history-preserving
source remains preserved separately in PR #448.

The [fresh-start driver](../../../tempo-cutover-transition.mjs) is an extension
of the existing launcher component, with no CLI or production wiring. It reuses
public `acquireTargetLock` and `atomicJson` from
[the existing deployment component](../../../tempo-deployment.mjs). Current
normal launch and Study remain unchanged. The retained `executionScope:
"synthetic-fixture"` path remains separate. Installed effects require the explicit
`createInstalledFreshStartEffects` factory and every independently verified public
operation; a raw installed scope label still reports `graph-contract-pending`
before acquiring a lock or changing files. A missing public fresh-Learning
initialization port reports `fresh-initialization-contract-pending`.

## Implemented sequence and ownership

```mermaid
sequenceDiagram
    participant C as Existing launcher composition (unwired)
    participant L as Fresh-start lifecycle
    participant S as Original maintenance lock and private journal
    participant G as Injected public graph and Learning capabilities
    participant P as Private public-contract admission packet
    participant A as Public installed-authority reader
    C->>L: Explicit fresh choice, graph source, pinned new target
    L->>S: Acquire original lock; persist transition identity
    L->>G: Verify source graph closure
    L->>G: Verify immutable full graph/source recovery COPY
    L->>G: Provision independent clean Learning target
    L->>G: Import graph through public serialized interface
    L->>G: Initialize fresh Learning state and verify isolation
    L->>G: Verify graph semantics in quarantine
    Note over L,G: Original authority remains active and untouched during preparation
    L->>G: Finalize pinned pair from public graph/init receipts
    L->>G: Specific cutover confirmation before authority switch
    L->>S: Persist admission-started
    L->>G: Fence original ingress/writers
    L->>S: Durably publish matching registration and receipt
    L->>G: Separate fresh-target admission release gate
    L->>S: Persist activation-started
    L->>G: Open selected fresh target and verify
    L->>S: Persist complete journal; writeCompletedAdmission(plan)
    S->>P: Durably publish verified plan/evidence/facts
    Note over L,S: Release original lock after publication
    A->>P: Read exact selected admissionPath
    P-->>A: Public packet; no raw journal
```

Lifecycle owns phase and publication state. Injected capabilities own graph
source/closure, recovery copy, target provisioning, public import, fresh
initialization, authority switching and admission. The graph adapter consumes
the owning Content/graph public serialized CLI/HTTP interface and public
SourceSnapshot v1 contracts; no bridge execution internals, converter internals,
SQL, sibling plugin implementation or optional service is imported here.

`provisionCleanTarget` must set up the explicit selected schema and workspace
through the existing public Learning migration contract before graph import;
the installed public graph bridge cannot import into an uninitialized schema.
Provisioning and quarantine evidence must report that exact pinned schema.
For v2, `registration.cutover.schema`, `receipt.cutover.schema` and
`receipt.schema` are one identical `sha256:` plus 64 lowercase hexadecimal
characters. This identity is supplied by the database owner's public
`schema_identity` contract: SHA-256 of its canonical sorted, compact UTF-8
migration-ID-to-checksum map, issued only when the actual map equals the
expected map. The outer adapter maps the public provisioning
`receipt.schema.identity` scalar into `fact.schema`; provisioning and quarantine
must match the pinned scalar exactly. `migration_count` is informational and
never substitutes for schema identity. The lifecycle does not import owner
internals or recompute migration semantics. Original v1 publication records
retain their numeric schema fields without conversion.
The later fresh-initialization port verifies fresh Learning state and authority
isolation after import; it does not guess or implicitly initialize a schema.

The verified immutable recovery copy must preserve the full authored graph,
names/source data and required closure. Reduced Learning graph rows alone are
insufficient. The exact installed graph closure and public graph/fresh-init
receipt contracts remain external inputs; the driver never manufactures them.
`createVerifiedGraphCopy` consumes and verifies an existing immutable graph
recovery bundle/receipt from the sole authorized capture executor; the bundle
may have been prepared earlier. This capability does not authorize a second
live capture or require a full-history backup. The lifecycle has no capture
adapter. All executed capabilities in this packet are disposable protocol
fixtures; no actual installed operation or accepted graph receipt was created.
The fresh initialization owner must verify a new configured workspace/origin/
token authority isolates old browser authority. No unsupported authority-epoch
runtime setting, old-browser compatibility, history replay or drain is added.

The initial intent includes `version`, `transitionId`, `revision`,
`sourceIdentity`, `targetIdentity`, `lockIdentity: sourceIdentity`,
`freshStartChoice: "graph-preserving-fresh-start"`, selected `registration` and
`receipt`, explicit prior publication presence in `original`, and opaque public
`graphSource`, `graphVerification`, `freshInitialization` inputs. The selected
pair shares a `cutover` identity containing the fresh-start mode and pinned
`graphSourceReference`. Its `graphReceipt` and `freshInitReceipt` may remain null
until the injected finalizer verifies graph import and fresh initialization;
only these two references may change during finalization. A known absent old
deployment receipt is valid and is recorded as null; it is not a backup or
verified-old-deployment prerequisite.

## Gates and interruption safety

`assertReleaseGates(plan, checkpoint, facts, gateContext)` receives the finalized
selected pair once available. The result must be ready and include an evidence
reference plus exactly `{version: 1, checkpoint, gateContext, planSha256}`.
`planSha256` hashes complete canonical JSON (object keys recursively sorted,
arrays ordered, JSON scalars only). `gateContext` is freshly generated under the
existing source lock on every invocation. The nonce rejects stale or miswired
verdicts; it does not replace actual graph or approval evidence.

`before-preparation` authorizes fixture preparation. `before-authority-switch`
requires graph recovery/target verification and exact user cutover confirmation
before original fencing or publication. `before-admission` controls opening the
fresh target after durable pair publication. `before-resume` freshly observes
this transition's target and writer state; original fencing is required only
once its fence phase completed. `before-forward-recovery` preserves effects
already accepted by the fresh target, without reviving old authority.

Every effect has a durable started marker and a transition/phase operation key.
Interrupted effects require their public operation receipt or positive proof
they never started; unknown completion blocks, without resetting/reprovisioning
a possibly populated target. The journal preserves prior publication bytes and
digests only for crash/mismatch detection, with private mode `0600`, file fsync
and directory fsync. There is no old-data restoration or deletion capability.
Never publish this private journal or observed graph/source bundles to GitHub.

A split registration/receipt pair blocks admission. A matching pair requires
fresh file/directory durability confirmation. `activation-started` is persisted
before target writes/maintenance/traffic. Uncertain or confirmed fresh-target
effects require explicit forward repair of that same target; no automatic
fallback or restoration of an older graph copy is implemented. Preparation
never stops or changes the original database/services, and actual switching
remains separately authorized.

## Installed producer and public completion

`createInstalledFreshStartEffects({operations, readReleaseEvidence,
assessReadiness})` snapshots the named owner functions and creates a nominal
capability. Copying its properties or setting `executionScope: "installed"`
does not opt into execution. The composition supplies the public
`evaluateInstalledFreshStartReadiness` evaluator with independent observed graph
and fresh-initialization verifiers; the original synthetic evaluator remains
production-blocked. Exact user confirmation is required even before installed
preparation. No actual values or approval records are supplied by this source.

Each installed owner returns `scope: "observed"`, `verified: true`, a nonblank
evidence reference and the supplied complete-plan/operation/lock-nonce binding.
Final graph and fresh-init references must match their owner facts before
authority switching. Admission requires separate observed completed readback,
not merely a successful command or user choice. The private journal records
installed provenance, final release evidence and completion; a synthetic journal
cannot be promoted by adding a scope label.

The state owner's `readCompletedAdmission(plan)` checks its complete journal and
both exact publication records, then returns the existing public packet
`{plan, evidence, facts}`. `verifyInstalledAdmissionPacket(packet)` validates that
packet and projects the completed source/lock/target identity for the public
authority reader. `facts.admission.binding` reuses
`readinessBinding(finalizedPlan, "before-admission", gateContext)` from the public
readiness protocol. No parallel hash, journal parser or history-transfer model
is introduced. The ordinary launcher consumes this packet but never invokes
the one-time transition implicitly. Consumers must independently reobserve the
current daemon, source writer fence, physical target and runtime lineage.

Installed execution requires an explicit `admissionPath` on `createCutoverState`
before any owner operation, canonically equal to the `installedStudy.admissionPath`
already pinned in the selected registration. The carrier's original lock/state
and receipt paths must match the owning state capability too. The separate
`installedStudy.originalRegistrationPath` must already contain private preserved
v1 metadata exactly equal to the planned original registration; it cannot point
to the configuration that publication will rewrite. Missing or mismatched input
blocks execution; the producer never fabricates that preservation evidence.
The admission parent must be owned and private, the destination
must be outside the source/target/executing checkouts, and it cannot alias a
publication, journal or lock. Links and unrelated existing outputs are refused.
`writeCompletedAdmission(plan)` derives its packet from that owner's completed
journal; it never accepts a caller-invented packet. Existing `atomicJson` provides
private-file and parent-directory durability. Publication occurs only after
the complete journal. If that final file write fails, the normal command provider
stays blocked; an explicit completed resume can publish without repeating target
activation or reverting authority. An identical already-published packet is an
idempotent no-op.

These validators establish source protocol correspondence. They cannot replace
the actual owner observations, complete graph readback or separately authorized
cutover that must produce the real evidence.

## Focused verification

Run `node --test scripts/tests/tempo-cutover-transition.test.mjs`. The owning
suite uses task-owned temporary files and fake public capabilities, removes all
temporary directories, and covers full-copy/closure guards, independent clean
target, explicit fresh choice, known-null receipt, graph/init identity binding,
separate confirmations, interruption reconciliation, split publication,
durability and preservation of newly accepted effects during forward repair.

The added installed-protocol suite is
[`tests/runner/tempo-study-admission.test.mjs`](../../../../tests/runner/tempo-study-admission.test.mjs).
It uses injected observed-protocol fakes and disposable files only, including
completed-packet projection, nominal capability checks, existing synthetic-mode
behavior, exact authorizations, stage bindings, durable pair protection,
interruption and explicit forward recovery. Earlier 37-case synthetic lifecycle
and 38-case readiness evidence remains recorded at its qualified source; those
suites are not repeated merely for documentation or registration.

No old artifact suites, real database/browser/service work, snapshots,
deletion/reset, migration, deployment or cutover are run. Actual installed graph
closure/source evidence, graph/fresh-init public acceptance and the exact user
cutover confirmation remain explicit external blockers.
