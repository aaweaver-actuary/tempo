<div id="title-block-header">

# Backend domain audit

</div>

- <a href="#tempo-backend-domains-first-semantic-audit"
  id="toc-tempo-backend-domains-first-semantic-audit">Tempo backend
  domains: first semantic audit</a>
  - <a href="#main-conclusions" id="toc-main-conclusions">Main
    conclusions</a>
  - <a href="#measured-source-areas" id="toc-measured-source-areas">Measured
    source areas</a>
  - <a href="#five-concrete-responsibility-splits"
    id="toc-five-concrete-responsibility-splits">Five concrete
    responsibility splits</a>
  - <a href="#overlapping-stacks-and-what-the-evidence-establishes"
    id="toc-overlapping-stacks-and-what-the-evidence-establishes">Overlapping
    stacks and what the evidence establishes</a>
  - <a href="#exact-duplicate-evidence"
    id="toc-exact-duplicate-evidence">Exact duplicate evidence</a>
  - <a href="#dry-abstractions-that-would-actually-help"
    id="toc-dry-abstractions-that-would-actually-help">DRY abstractions that
    would actually help</a>
  - <a href="#proposed-smallest-useful-organization"
    id="toc-proposed-smallest-useful-organization">Proposed smallest useful
    organization</a>
  - <a href="#first-bounded-next-work"
    id="toc-first-bounded-next-work">First bounded next work</a>

# Tempo backend domains: first semantic audit

**Pinned source:** `7d77ed1b25198dcd320e0265f777cc630f23775e` (main
observed October 21, 2026). Read-only source inspection. No application
execution, live database queries, deployments, migrations, source
changes, or user-data changes were performed.

**Coverage:** selective semantic review of the largest mixed non-Study
workflows, complete reads of several small service entrypoints and the
legacy game scheduler/import workflow, import searches, and a
reproducible exact-AST duplicate-function scan. This is not a claim that
every line of the repository has been reviewed. `backend-reviewed.json`
records file-level depth and inspected ranges;
`backend-exact-duplicates.json` records mechanical matches separately.

## Main conclusions

1.  **The substantial reduction opportunity is retiring overlapping
    implementations and unselected product capabilities.** Simple
    extraction of SQL or movement into logical folders has improved some
    boundaries but has not reduced the number of supported paths. The
    legacy backend, independent Learning, Content, Game Evidence,
    Intelligence, and evaluation packages coexist. Their names overlap,
    but their wire identities, persistence models, and adoption status
    differ; no whole stack is declared dead by this audit.
2.  **Some filenames conceal their actual authority.**
    `game_sync_coordinator.py` runs seven kinds of background work,
    including integrity, priority calculation, durable tasks, and
    opening-graph work. It is not safe to remove everything called Games
    merely because Study should eventually be independent.
3.  **Large functions still combine read orchestration, chess policy,
    presentation, side effects, and durable jobs after their SQL has
    moved out.** This is especially visible in
    `repertoire_opportunities.py`, `discovery_admission.py`, and
    `introduction_priorities.py`.
4.  **DRY work can help but cannot credibly deliver 50% by itself.** An
    exact-AST scan found 16 duplicate function groups with each function
    at least 20 physical lines. Summing the first copy's physical span
    once per extra copy gives 710 lines. This is a conservative
    mechanical detection metric, not a deletion estimate and not a total
    duplication measurement. The important duplication is also whole
    alternate workflows with different semantics, which exact matching
    cannot count.
5.  **The existing inventory should be extended, not replaced with a
    second authority.** `backend/ORGANIZATION.md` and
    `backend/organization-inventory.json` already account for 202
    original modules (29 moved intact, one transport split, three
    partial extractions, 169 awaiting owner closure). Those are
    inherited review claims, not this audit's new semantic coverage.
    Several README files still describe current-main code as “unmerged”;
    that stale status increases re-entry cost.

## Measured source areas

Counts below are physical lines, including comments and blank lines, at
the pinned commit. Only the listed Python source trees are counted;
tests, native database code, lockfiles, fixtures, generated artifacts,
and frontend code are excluded. These figures must not be added to a
broader census that already includes them.

| Area                            | Python files | Physical lines | Interpretation                                                                                                                   |
|---------------------------------|-------------:|---------------:|----------------------------------------------------------------------------------------------------------------------------------|
| `backend/app`                   |          234 |         40,133 | Legacy HTTP/application layer plus newly organized leaves. Contains Study dependencies until deployment identity is established. |
| `services/learning/runtime/src` |           54 |          8,175 | Independent Learning serving code; protect if this is the working Study service.                                                 |
| `services/content/src`          |           19 |          1,761 | Structured content, inventory and publication workflows; source presence does not prove deployment.                              |
| `services/game-evidence/src`    |           18 |          2,180 | Source ingestion, comparison snapshots, durable observations and delivery.                                                       |
| `services/intelligence/src`     |           36 |          3,281 | Explorer/Maia evidence and Analysis APIs/workers.                                                                                |
| `packages/evidence-math/src`    |           10 |          2,423 | Independently packaged pure ranking math; retained. Its offline ranking-evaluation consumer is retired by candidate batch ranking-evaluation-01.           |
| `tools/ranking-evaluation/src` (retired candidate) | 0 | 0 | Former offline replay/evaluation tool; 8 Python modules / 772 production lines removed. See RETIREMENT-BATCHES.json. |

Two disjoint legacy review cohorts:

- **4,056 lines:** `repertoire_opportunities.py` (991),
  `introduction_priorities.py` (933), `repertoire_coverage.py` (708),
  `discovery_admission.py` (648), and `repertoire_integrity.py` (776).
  This is a source-review target, not 4,056 confirmed deletable lines.
  Integrity eligibility in particular can protect current cards.
- **1,789 lines:** `game_sync.py` (224), `game_sync_coordinator.py`
  (524), `game_findings.py` (467), `gameplay_events.py` (244), and
  `repertoire_comparison.py` (330). This set contains common background
  infrastructure and legacy Study call paths; the label “Games” is not a
  deletion criterion.

## Five concrete responsibility splits

These proposals apply only to responsibilities we choose to retain. If a
workflow is retired, deleting it is cheaper than first moving it into an
elegant new hierarchy. SQL adapters already in `database/` should keep
their transaction authority; physical organization is not permission to
create a second writer.

### 1. `backend/app/services/game_sync_coordinator.py` — 524 lines

**Observed functionality, in order:**

- Lines 53–66: global task-handler and maintenance-handler registries.
- Lines 73–135: enqueue, claim, complete/fail, retry and execute
  provider-sync jobs.
- Lines 138–217: enqueue game derivations, request priority/opportunity
  refreshes, checkpoint derivation phases and pause/resume.
- Lines 221–295: seven-phase game workflow: index positions, compare
  repertoire, derive findings, apply real-game misses, derive gameplay
  events, refresh statistics and request priorities.
- Lines 298–320: chess replay to construct persisted position
  occurrences.
- Lines 323–352: asynchronous lifecycle plus `ProcessPoolExecutor`
  ownership.
- Lines 358–509: a round-robin scheduler across
  durable/sync/derivation/coverage/integrity/statistics/priority,
  special opening-graph and priority process-pool execution, progress
  reporting, recovery behavior, and maintenance handlers.
- Lines 513–524: startup recovery for several unrelated work families
  and a global coordinator singleton.

**Proposed destinations:**

| Responsibility                                                   | Suggested retained home                                                                                         |
|------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| Shared background lifecycle, wake signal and fair work selection | `backend/app/operations/background_runtime.py` or existing equivalent, composed from explicit task capabilities |
| Import-job commands and execution                                | `backend/app/games/import_jobs.py`                                                                              |
| Game derivation sequence and checkpoint policy                   | `backend/app/games/derivation.py`                                                                               |
| Pure game replay/index facts                                     | `backend/app/games/ingestion/positions.py` or the existing ingestion package                                    |
| Graph/priority worker callables                                  | Their actual opening/authoring owners; passed into the outer runtime                                            |
| Recovery registration                                            | Composition root, using explicit named owner recovery functions                                                 |

**Why this split matters:** importing a Games coordinator currently
imports coverage, integrity, priority, statistics, findings and graph
workflows. Learning's legacy review adapter also lazily imports the
coordinator (`learning_review_adapter.py:201,224`). If a supported
legacy serving path uses that adapter, removing this file breaks
ordinary Study-related work even though it is named Games. A tiny
explicit list of task capabilities is enough; a generic plugin/job
framework is not needed. Preserve singleton/lock/timing identities while
it remains active.

### 2. `backend/app/services/repertoire_opportunities.py` — 991 lines

**Observed responsibilities:**

- Lines 37–119: settings, identities, current-scope publication,
  dismissal/reopening decisions, and downstream task submission.
- Lines 123–350: route-success evidence, recurring weak decisions,
  thresholds and publication effects.
- Lines 351–535: coverage gaps and post-gap weakness opportunities.
- Lines 536–712: refresh admission, six-phase durable cursor workflow,
  generation fencing, cleanup, and daily-queue requests.
- Lines 715–860: cached accepted-move projection, source-game replay,
  route reconstruction and UI response formatting.
- Lines 863–898: dismiss/acknowledge/snooze commands.
- Lines 901–991: training eligibility, prefix splitting, card
  introduction and direct queue admission.

**Proposed split:** `authoring/discovery/scoring.py` for calculation
over supplied facts; `refresh.py` for the bounded workflow; `queries.py`
for the discovery feed; `commands.py` for dismissal/snooze;
`training.py` for the explicit command to Learning. Keep fixed SQL/read
projections in the existing database owner. Do not move `_publish` into
a supposedly pure scoring module: it owns transactions, scope checks and
task effects. `scoring.py` should receive an explicit `as_of`;
`_calculate_node_opportunities` currently calls the wall clock at line
396.

**Deletion angle:** if Discoveries is not a selected usable feature,
retire its HTTP surfaces and refresh registrations together, then remove
the now-unreferenced scoring/feed/jobs and their projections. Do not
keep a permanently dormant refresh pipeline in the serving dependency
graph. Do not remove the actual Learning admission authority: optional
producers should issue semantic requests to that owner.

### 3. `backend/app/services/discovery_admission.py` — 648 lines

**Observed responsibilities:**

- Lines 34–169: identities, chess similarity, source game/coverage-route
  selection.
- Lines 170–235: full-history engine request construction and
  recommendation request jobs.
- Lines 237–270: cached repertoire position examples.
- Lines 272–405: database reads, saved engine-report validation, sound
  continuation filtering, transposition/familiarity ranking,
  four-decision previews, and a UI response.
- Lines 411–475: idempotent admission intent identity, retry/priority
  behavior, current-evidence checks, intent storage and durable job
  submission.
- Lines 483–555: validate and write a new authored branch, update
  canonical scope positions, add training depth, and request
  opening-graph rebuild.
- Lines 558–587: start coverage refresh, with PostgreSQL and
  compatibility paths.
- Lines 590–648: wait for publication/integrity, introduce the resulting
  card, enqueue guided Study and retry unfinished work.

**Proposed split:** `authoring/discovery/recommendations.py` for
recommendation calculation over a supplied report and repertoire
examples; `recommendation_jobs.py` for obtaining evidence;
`admission.py` for the explicit approved-intent workflow; and an
existing Content/source authoring operation to persist the branch. The
final Study effect crosses the Learning command boundary. The outer
workflow may sequence those capabilities but should not acquire another
owner's SQL API.

**Important detail:** this workflow currently creates a branch and then
waits for graph, coverage and integrity work before a card can be
admitted. That is a concrete reason one apparently simple user action
can require understanding many systems. A future simplified authoring
path should publish an executable definition once, then request Learning
admission. That is a behavior/interface redesign, not a safe mechanical
move; it must preserve existing card identity/history and explicit
approval.

### 4. `backend/app/services/introduction_priorities.py` — 933 lines

**Observed responsibilities:**

- Lines 40–84: input/result dataclasses.
- Lines 90–470: route construction, probability evidence and graph/card
  mapping.
- Lines 472–546: a mostly calculation-only priority policy (completion
  mass, frontier reach and real-game-miss bonus).
- Lines 549–635: materialize calculation inputs and publish
  compatibility records.
- Lines 638–673: synchronous compatibility path explicitly documented as
  used by parity tests.
- Lines 676–757: admission, refresh coalescing, generation and job
  claim.
- Lines 759–837: read/compute/publish orchestration, timing logs,
  retries and downstream discovery refresh.
- Lines 840–899: process-worker input wrapper and staged generation
  publication.
- Lines 902–933: status projection.

**Proposed split:** `authoring/priorities/model.py`, `calculation.py`,
`refresh.py`, and `queries.py`, with input/storage operations remaining
in database adapters. The synchronous compatibility path should be
retired if its only remaining callers are retired tests; retain
behavioral regression assertions against the surviving
calculation/publisher instead of deleting safeguards to make the count
fall.

**Deletion angle:** if new-card order is intentionally frozen and no
accepted workflow needs adaptive refresh, this is a high-value
retirement candidate after tracing refresh consumers and queue
fallbacks. Existing frozen rank rows, ordinary eligibility and current
queue behavior must remain. “Not recomputed this week” alone does not
prove the reader or fallback is unused.

### 5. `backend/app/services/repertoire_integrity.py` — 776 lines

**Observed responsibilities:** deterministic source legality/conflict
checks; synchronous aggregate scans; per-card eligibility blocks;
durable sliced scans and retry state; summary/UI projections;
source/card rewrites, canonical-scope validation, derived-card
reconciliation and durable repairs.

Concrete evidence: `_scan_source` (70–117), `_scan_sources` (120–152),
`_issues_from_observations` (285–311), and `resolve_issue` (587–637).
`_scan_sources` and `_issues_from_observations` duplicate issue
finalization but differ in how they obtain/group observations.
`resolve_issue` validates one selected move and then rewrites lines and
cards, reconciles derived cards, certifies routes and rescans.

**Proposed split:** `authoring/integrity/validation.py` (pure source
observations and finalization), `scan.py` (bounded workflow),
`queries.py`, and `repair.py`. A single small pure finalizer can serve
direct and staged scans. Preserve eligibility protections for existing
Study cards even if the interactive repair UI is retired. Do not delete
integrity by classifying it as an optional Builder feature.

## Overlapping stacks and what the evidence establishes

| Capability                                  | Existing parallel implementation                                                                                                                                                                     | What to decide before deletion                                                                                                                                                                                                                                           |
|---------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Provider game ingestion                     | Legacy `app/games/providers/*` + `services/game_sync.py`; newer `services/game-evidence/.../providers.py` and source ingestion packages                                                              | One active import interface, game identity mapping, raw-source retention and exactly-once/duplicate handling. They have different pagination/checkpoint logic; they are not interchangeable copies.                                                                      |
| Repertoire comparison and gameplay findings | Legacy `repertoire_comparison.py`, `game_findings.py`, `gameplay_events.py`; newer Game Evidence comparison and Intelligence Analysis findings                                                       | Keep one source of comparison truth and one selected feedback path. Engine mistake != repertoire deviation; preserve that distinction without retaining two independently evolving implementations.                                                                      |
| Explorer evidence                           | Legacy `repertoire_coverage.py` embeds credential/session handling, provider HTTP, cache, work leases, calculations and feed reads; independent Intelligence separates source evidence from Analysis | Confirm which path supplies current reads. Retire provider/cache/jobs as a unit after replacement; do not force Study to call the replacement provider.                                                                                                                  |
| Opening ranking math                        | Legacy opening probability/reach/readiness/valuation modules and `packages/evidence-math`                                                                                                            | The offline `tools/ranking-evaluation/src` consumer is retired by candidate batch ranking-evaluation-01; packaged math and live legacy ranking remain. This does not authorize retiring legacy copies or changing DTO/model identity. |
| Chess geometry and segmentation             | Legacy `app/chess/*` and independent Learning helpers                                                                                                                                                | Exact function bodies overlap, but owner model classes differ. Prefer a tiny neutral pure dependency only if it saves more complexity than it introduces; do not alter working Learning merely to reduce a few dozen lines.                                              |
| Future classification/actions packages      | `packages/game-classification/.../__init__.py` and `packages/game-actions/.../__init__.py` each implement an explicit Unavailable boundary, nine lines each                                          | These are not working features. Retirement may remove manifests/test/build obligations, but deleting 18 source lines is not a meaningful code-reduction milestone.                                                                                                       |

## Exact duplicate evidence

The JSON appendix contains all 16 matches and line ranges. Particularly
useful ones:

- `propagate_policy_reach`:
  `backend/app/study/openings/policy_reach.py:251–359` and
  `packages/evidence-math/.../_policy_reach.py:253–361`.
- `_validated_recall_facts`:
  `backend/app/services/decision_readiness.py:165–261` and
  `packages/evidence-math/.../_readiness.py:190–286`.
- `_validate_inputs`:
  `backend/app/services/opening_card_valuation.py:117–208` and
  `packages/evidence-math/.../_valuation.py:134–225`.
- `defensive_fork_geometry`:
  `backend/app/chess/threat_geometry.py:21–66` and
  `services/learning/runtime/.../threat_detection.py:10–59`.
- `schedule_review`: `backend/app/services/scheduler.py:8–37` and
  `services/learning/runtime/.../scheduler.py:9–46`.

The scan parsed Python with `ast.parse`, compared
`ast.dump(..., include_attributes=False)` for functions/async functions
of at least 20 physical lines, and excluded `tests`, `integration`,
`tooling` and `fixtures`. For `services/` and `packages/`, only `src/`
was scanned. It also scanned `backend/app` and `database`. It does not
normalize identifiers or imports, find near-duplicates, prove equivalent
global dependencies, or establish runtime reachability. It executes no
repository code.

## DRY abstractions that would actually help

**Prefer reuse of existing pure calculation packages or small named
helper functions.** The issue is not lack of decorators. The code
already uses dataclasses, Pydantic validators, cached pure snapshots and
route decorators.

Good bounded opportunities:

- A pure issue finalizer for the two integrity scan flows.
- A same-owner canonical cursor codec only after exact bytes and errors
  are compared (Analysis `games_reader.py` versus `read_work.py`).
- A small timing context/decorator for optional worker measurements,
  preserving return values, exceptions, cancellation and
  `functools.wraps`. It should log, not decide whether a task is
  complete.
- One SQL operation for identical same-owner reads, while keeping
  locking/nonlocking variants explicit.
- Reuse a demonstrated pure math implementation rather than copying it
  into another service.

Avoid generic transaction, receipt, retry, or publication decorators
across owners. The current code's hard parts are which transaction
commits the user effect and receipt, which generation may publish, which
lock comes first, and what cancellation means. Hiding these decisions
would reduce visible lines while making agent comprehension and
data-safety review harder. Use an explicit operation or context manager
with a narrow documented contract when it owns resource lifetime.

## Proposed smallest useful organization

Retain current independent Learning and Study paths while they serve the
user. For optional features selected for continued development, reuse
existing owners and expose only the small semantic boundary that is
actually consumed:

| Owner                   | Allowed knowledge                                                                     | Excluded knowledge                                                    |
|-------------------------|---------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| Study practice/Learning | Execute saved cards, schedule reviews, select new cards, persist attempts and retries | Games imports, engines/providers, discovery jobs, authoring lifecycle |
| Content/authoring       | Source lines/PGN, executable definitions, validation, publication                     | Study sessions or raw scheduling SQL                                  |
| Games                   | Imported records, comparisons against supplied accepted snapshots                     | Direct queue/review writes                                            |
| Analysis                | Optional provider/engine evidence and findings                                        | Required Study startup or readiness                                   |
| Database adapters       | Complete owner-specific queries and transaction mechanics                             | Another owner's decisions or borrowed generic SQL ports               |
| Composition/operations  | Wire capabilities, processes and resource lifecycles                                  | Business rules buried in the application root                         |
| Pure libraries          | Chess identity, legal calculations and genuinely shared deterministic policy          | Database, HTTP, worker startup or global live state                   |

A logical module is a coherent responsibility, not necessarily one
function, one file, one wheel, or one microservice. Avoid creating an
interface, DTO module, command dispatcher and service container for
every helper. Maintain one short public map per retained owner and a
generated function index for deeper lookup.

## First bounded next work

1.  Verify the actual phone and desktop Study build, API origin,
    process/image, database owner, and frozen-order serving path; source
    targets alone do not identify deployment. Protect that closure.
2.  Choose a single supported backend topology. If independent Learning
    is already serving both clients, the legacy stack becomes a large
    retirement investigation. If not, freeze legacy serving first and do
    not couple retirement to a large Study rewrite.
3.  Classify optional feature families as keep now, retained offline
    tool, or retire. For each retire decision remove
    entrypoints/registrations, then runtime code and exclusive
    tests/build/docs; preserve necessary regression invariants on the
    surviving implementation. Git history/tag is the archive, not a
    second active source tree.
4.  Only split the retained mixed hubs above. Require each resulting
    file to have a single clear purpose and no eager import of optional
    sibling implementations.
5.  Retire exclusive projections/tables after data retention and live
    ownership are verified separately. Do not combine code
    reorganization with a broad table-drop migration.

The recommendation is an evidence-based simplification program with a
numeric source baseline, not a claim that 50% is already proved
removable. The likely failure mode is spending another large cycle
organizing every old path while retaining all of them. The
counterargument is that some of the currently unusable features have
valuable algorithms and near-complete independent implementations;
retaining selected source packages can be cheaper than rebuilding them.
The decision should follow an explicit small active feature set, not
sunk implementation effort.