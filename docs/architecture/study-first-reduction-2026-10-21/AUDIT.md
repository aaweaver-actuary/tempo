<div id="title-block-header">

# Tempo code reduction audit — October 21, 2026

</div>

- <a href="#tempo-code-reduction-audit"
  id="toc-tempo-code-reduction-audit">Tempo code reduction audit</a>
  - <a href="#recommendation" id="toc-recommendation">Recommendation</a>
  - <a href="#1-the-measured-baseline" id="toc-1-the-measured-baseline">1.
    The measured baseline</a>
  - <a href="#2-the-essential-decision-which-study-stack-is-serving"
    id="toc-2-the-essential-decision-which-study-stack-is-serving">2. The
    essential decision: which Study stack is serving?</a>
  - <a href="#3-first-file-by-file-findings"
    id="toc-3-first-file-by-file-findings">3. First file-by-file
    findings</a>
  - <a href="#4-database-findings" id="toc-4-database-findings">4. Database
    findings</a>
  - <a href="#5-a-simpler-target-organization"
    id="toc-5-a-simpler-target-organization">5. A simpler target
    organization</a>
  - <a href="#6-where-dry-abstractions-help"
    id="toc-6-where-dry-abstractions-help">6. Where DRY abstractions
    help</a>
  - <a href="#7-the-proposed-step-by-step-reduction-program"
    id="toc-7-the-proposed-step-by-step-reduction-program">7. The proposed
    step-by-step reduction program</a>
  - <a href="#8-acceptance-for-deletion-batches"
    id="toc-8-acceptance-for-deletion-batches">8. Acceptance for deletion
    batches</a>
  - <a href="#9-how-to-use-the-running-inventory"
    id="toc-9-how-to-use-the-running-inventory">9. How to use the running
    inventory</a>
- <a href="#appendix-a--complete-sequential-mainpy-review"
  id="toc-appendix-a--complete-sequential-mainpy-review">Appendix A —
  Complete sequential main.py review</a>
  - <a href="#finding" id="toc-finding">Finding</a>
  - <a href="#ordered-responsibility-map"
    id="toc-ordered-responsibility-map">Ordered responsibility map</a>
  - <a href="#three-causes-of-disproportionate-complexity"
    id="toc-three-causes-of-disproportionate-complexity">Three causes of
    disproportionate complexity</a>
  - <a href="#protect-split-or-retire"
    id="toc-protect-split-or-retire">Protect, split, or retire</a>
  - <a href="#concrete-next-sequence"
    id="toc-concrete-next-sequence">Concrete next sequence</a>
  - <a href="#appendix-b--every-function-in-mainpy"
    id="toc-appendix-b--every-function-in-mainpy">Appendix B — Every
    function in main.py</a>

# Tempo code reduction audit

**Running audit · revision 1 · October 21, 2026**  
**Source:** current `main` observed at
[`7d77ed1b25198dcd320e0265f777cc630f23775e`](https://github.com/aaweaver-actuary/tempo/commit/7d77ed1b25198dcd320e0265f777cc630f23775e).
Every source count and line reference below belongs to this immutable
revision.

## Recommendation

**Reduce the number of implementations and supported workflows before
reorganizing the survivors.** The strongest route toward a 50%
backend/database reduction is to retire a parallel implementation stack
once the serving boundary confirms it is unnecessary, along with
unselected feature pipelines. Another broad exercise in moving every
existing function into a new folder would preserve most of the cost that
Andy wants removed.

Keep the working Study implementation, its data, phone behavior,
queue/review rules, and recovery paths protected. Determine which
serving stack actually provides it. Then remove complete, unneeded
workflows outside that boundary. Apply small abstractions and logical
module splits only to code that earns a place in the surviving product.

This audit starts the requested file-by-file review. It contains a
complete mechanical repository and Python-definition index, a
reconstructed source-schema table inventory, selected substantive
reviews, and a full sequential read of the largest HTTP module. It does
**not** claim that all repository lines have received semantic review,
that every table is installed, or that a particular deletion has already
been demonstrated safe against live Study. No application code, running
service, live database, migration or deployment was changed. No
application/infrastructure tests were run.

## 1. The measured baseline

Counts are physical source lines, including blank lines and comments, at
the pinned revision. Tests, tooling, build/qualification helpers,
examples, prose, lockfiles and binary assets have separate
classifications. These are size and navigation measures, not
executed-line or complexity measures.

| Scope                                                                                   |                    Files / definitions |                     Physical lines |
|-----------------------------------------------------------------------------------------|---------------------------------------:|-----------------------------------:|
| All tracked repository files                                                            |                            4,012 files |        See complete file inventory |
| All Python source, including tests/tools                                                | 1,331 parsed files; 14,987 definitions | Complete definition index included |
| Legacy `backend/app` Python                                                             |                              234 files |                             40,133 |
| Database runtime/schema Python + SQL                                                    |                              363 files |                             29,553 |
| **Primary backend/database Python + SQL baseline**                                      |                          **597 files** |                         **69,686** |
| **50% reduction against that baseline**                                                 |                     Net removal target |                         **34,843** |
| Broader implementation/schema Python + SQL across six server/shared roots               |                              791 files |                             91,624 |
| Broader server/shared runtime/schema source, including JavaScript/TypeScript/Rust/shell |                              881 files |                             97,217 |

The broader scope is `backend`, `database`, `services`, `packages`,
`workers`, and `contracts`. Some browser/shared storage code is in that
881-file scope; it is not all backend code. The 14,987 Python
definitions comprise 13,405 normal functions, 177 async functions and
1,405 classes. The file and symbol indexes explicitly distinguish
automatic indexing from substantive review. Forty-three identified
build/test/example/utility files were reclassified after independent
methodology review; the correction file is included.

**Do not game the target.** Moving code from `backend/` into `services/`
does not count as a reduction. Tests, documentation and generated files
are outside the implementation denominator. Keep the same physical-line
method for before/after comparisons, but report formatting-only or
comment-only removal separately from functional simplification. Record
net changes in both the narrow and broad scopes. Removing unsupported
test/build surfaces is a separate, useful reduction in maintenance cost.

### What has actually been read

The consolidated review ledger contains 125 distinct inspected files: 74
complete source/document reads (15,047 physical lines), 20 partial
reads, and 31 structural/AST comparisons. The 74 include documentation
and build files; 50 are implementation/schema modules in the broad
881-file scope. The remaining 785 modules in that scope have a
mechanical catalogue only. `main.py` received a complete sequential
read, with all 187 definitions individually described and 40 contiguous
responsibility ranges covering every line. These labels make the
unfinished work visible instead of treating a generated symbol list as a
completed semantic review.

### Why agents need so much context

The source contains two runtime architectures with very different import
closures:

| Entry point                                 | Files reached by syntactic imports | Physical lines in those files |
|---------------------------------------------|-----------------------------------:|------------------------------:|
| Legacy API, `backend/app/main.py`           |                                497 |                        63,550 |
| Legacy Celery tasks, `backend/app/tasks.py` |                                566 |                        70,403 |
| Independent Learning API                    |                                119 |                        14,150 |

These counts include imports inside functions, conditions and
type-checking branches; they can overstate actual execution. They omit
nonliteral dynamic imports, launchers, SQL relationships, browser/assets
and external dependencies; they can understate a deployed installation.
They are neither runtime measurements nor deletion certificates. They do
demonstrate the difference in the amount of source reachable from each
entry point. The complete import edges and limitations are provided for
reproducibility.

## 2. The essential decision: which Study stack is serving?

The independent composition already exists. `TEMPO_TARGET=study` selects
a small entry point; its build guard rejects named Games, authoring and
administration roots. The Learning image includes its review/remediation
applications, scheduling/chess libraries, publication/evidence contracts
and owned database packages, without the legacy backend, Redis, Celery
or engine workers. However, the root Compose file and default static
entry still select the legacy stack. Source alone cannot identify the
desktop and phone deployment. See the [Learning
Dockerfile](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/services/learning/runtime/Dockerfile),
[root Compose
file](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/docker-compose.yml),
and [Study composition
guard](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/scripts/study-composition.ts).

The phone distinction is concrete: `app/lib/offline-training.ts` retains
only the first issued card for independent Study, while the legacy path
retains the full prepared queue and can create local offline repeats. A
switch to the smaller stack is therefore not automatically
behavior-preserving. If Andy's current phone workflow depends on those
legacy capabilities, a cutover would be a separate product decision, not
incidental cleanup. [Offline
source](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/app/lib/offline-training.ts#L59-L82).

**Conditional conclusion:** if current phone and desktop Study already
use independent Learning, a major legacy retirement may achieve the
target without changing Study. If they use the legacy stack, a promise
to remove half while leaving Study entirely unchanged is not yet
supported. The first cuts must then remain outside the verified
protected dependency chain.

### Protected behavior and state

Preserve current repertoire content and membership; the next eligible
new move and frozen order; quotas and due reviews; attempt issuance and
teaching progress; grade/history/scheduling effects; queue progression
and wrong-answer repetition; retained phone work; replay after a lost
response; reload after a confirmed review but failed next-card load; and
conflict handling across phone/computer.

Protect accepted publication definitions and local remediation as well.
Ordinary Learning queue issuance, teaching and review touch those local
records. The Games producer can be optional while already accepted
obligations remain necessary. A contract named `game-evidence` is not
the Games implementation. Likewise, background maintenance that prepares
the daily queue is essential even though it is called background work.

## 3. First file-by-file findings

These are substantive starting points. More detailed ordered
function/range maps and exact review-depth records accompany this
report.

| Current module                                                                          | Lines | Responsibilities actually present                                                                                                                                                                                                              | Proposed disposition / logical separation                                                                                                                                                                                          |
|-----------------------------------------------------------------------------------------|------:|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `backend/app/main.py`                                                                   | 4,538 | HTTP setup and lifecycle; request priority and write-route eligibility; queue materialization/issuance; reviews; imports; repertoire authoring; discovery; engine evidence; Games; defense; statistics; guided review; background registration | Decide whether this entire legacy runtime survives. If it does, leave a thin composition root; move retained routers and application workflows to their actual owners. Never move the entire mixed hub and call the work complete. |
| `backend/app/tasks.py`                                                                  |   493 | Import-time command registration; Celery transport; foreground/background admission; receipts/retries; task selection/completion; queue wakeups; owner-specific maintenance                                                                    | Outer worker composition owns registration; a finite task specification can remove repeated kind/handler/completion lists. Retain the existing single execution and transaction authority.                                         |
| `backend/app/services/game_sync_coordinator.py`                                         |   524 | Seven-family background scheduler; Games import/derivation; coverage; integrity; statistics; priorities; process pool; maintenance/recovery                                                                                                    | Separate shared runtime from Games workflows if retained. It is a possible legacy Study dependency despite its filename.                                                                                                           |
| `backend/app/services/repertoire_opportunities.py`                                      |   991 | Evidence scoring, publication, sliced refresh, discovery feed, dismissal/snooze, queue admission                                                                                                                                               | Retire the entire Discovery slice if unselected. Otherwise separate scoring, refresh, queries, commands and Learning admission.                                                                                                    |
| `backend/app/services/introduction_priorities.py`                                       |   933 | Probability/route math, input snapshots, compatibility execution, durable refresh, publication generations and status                                                                                                                          | Compare against frozen-order needs and the packaged math. Retire unnecessary adaptive calculation; retain stored ranks, eligibility and necessary fallbacks. If kept: calculation, refresh and query modules.                      |
| `backend/app/services/repertoire_coverage.py`                                           |   708 | Credentials, provider HTTP, cache, leases, probability policy and UI projections                                                                                                                                                               | One optional evidence/coverage owner. Retire the old producer/cache/jobs together if another selected path owns the output.                                                                                                        |
| `backend/app/services/discovery_admission.py`                                           |   648 | Engine recommendations, transpositions, approved intent lifecycle, branch authoring, waits for graph/coverage/integrity, Study admission                                                                                                       | Split recommendation calculation, evidence acquisition and explicit acceptance workflow only if kept. Source authoring and Study admission cross their owners' semantic operations.                                                |
| `backend/app/services/repertoire_integrity.py`                                          |   776 | Legality/conflict checks, scans, eligibility blocks, summaries and source/card repair                                                                                                                                                          | Keep required eligibility protections. Separate pure validation, scan workflow, queries and repair; do not delete protection with an unused repair UI.                                                                             |
| `backend/app/services/postgres_queue_refresh.py`                                        |   827 | Durable queue phases with read/compute/write fencing, transaction slices and checkpoints                                                                                                                                                       | Treat as protected until serving is known. Its length alone is not a reason to split a coherent workflow or add a generic job framework.                                                                                           |
| `backend/app/study_routes.py`                                                           |   788 | Authored/imported Studies, exercise revisions/enrollment, presentation, attempts, self-assessment and feedback; 19 routes retain native-command and inline mutation alternatives                                                               | Separate authored content from enrolled exercise execution only after the legacy runtime decision. Preserve replay identity, current revision, cross-device review guards and scheduling authority.                                |
| `database/tempo_legacy_database/connections.py`                                         | 1,766 | Connection routing, admission/timing, PostgreSQL version check, historical SQLite schema upgrades, backfills, triggers and task seeding                                                                                                        | The PostgreSQL `initialize()` branch returns at line 205. If SQLite serving is retired, preserve any necessary offline converter and remove its runtime history. Keep connection authority explicit.                               |
| `database/tempo_legacy_database/adapters/authored_repertoires/opportunities/storage.py` |   625 | Feed/commands, summaries, game gaps, coverage facts, graph/card facts, refresh progress and admission                                                                                                                                          | Whole-feature retirement first; otherwise group cohesive queries, commands and refresh persistence. Avoid a file per query.                                                                                                        |
| `database/tempo_content_database/adapters/store.py`                                     |   607 | Connection scopes, staging/admission, inventory, route/exercise writes, publication, collection and pagination                                                                                                                                 | If Content survives, separate source, inventory and publication persistence behind a thin service; retain atomic publication.                                                                                                      |
| `services/learning/runtime/src/tempo_learning_runtime/api.py`                           |   738 | Composition/security, bootstrap/settings, queue issuance, reviews/evidence, several practice families and publication intake                                                                                                                   | Protected in first deletion batches. Record future router separation; do not start by refactoring working Study.                                                                                                                   |

The five legacy discovery/priority/coverage/integrity files above total
4,056 lines. That is a defined investigation cohort, not a measured safe
deletion set. The source already contains a useful [202-original-module
inventory](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/organization-inventory.json)
and [running simplification
list](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/database/SIMPLIFICATION.md).
This audit carries those inherited proposals forward while
distinguishing them from fresh source review.

The complete queue-refresh read found an especially misleading name:
`_admit_one_prioritized_opening` also publishes ordinary Study-only
openings. The Study-only branch calls it at lines 745–748. Retiring
optional ranking does not permit deleting that writer. Its work has
paged reads and finite transaction slices, but some preparation
accumulates whole candidate/queue sets; the source does not establish a
universal total-work bound. [Queue-refresh
source](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/services/postgres_queue_refresh.py#L735-L758).

### What the complete `main.py` pass establishes

The file contains 184 top-level function definitions plus three nested
definitions, 141 locally decorated HTTP routes and nine included
routers. It contains 92 `postgres_store.configured()` calls across 90
functions and 78 `dispatch_command` calls covering 72 literal operation
names. These are structural counts, not 90 duplicate implementations.

The 361-line middleware/decorator block at lines 421–781 manually
classifies route shapes for PostgreSQL writes, background admission and
query-only execution. The route declarations repeat those path concepts
elsewhere. `background_worker.py` imports `main` to obtain
registrations; queue GETs can issue attempts and therefore perform real
writes. These are concrete causes of navigation cost and unexpected
coupling. The main-file appendix maps all definitions and ordered
responsibility ranges.

## 4. Database findings

| Installation schema owner | Source table count |
|---------------------------|-------------------:|
| Legacy                    |                163 |
| Learning                  |                 70 |
| Content                   |                 20 |
| Intelligence              |                 25 |
| Game Evidence             |                 14 |

The table ledger has **292 owner-qualified source entries**, not 292
verified live tables. Legacy and Learning each include the shared
12-table remediation definition. Fifty-seven names overlap between those
two installation schemas; that indicates parallel generations, not that
two physical tables can simply be merged. The counts were reconstructed
from ordered checked-in migrations and compared with the owner indexes.
Live deployment/version/population remains unknown.

All seven `_legacy` tables are still read by source compatibility views
selecting generation-zero data and appear in historical import mappings.
For example, migration 009 creates `game_move_analysis` as a union over
the legacy and staged generations. Deleting the old physical table
without resolving that fallback breaks a reader. [Migration
evidence](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/database/tempo_legacy_database/ddl/migrations/009_versioned_game_analysis.sql#L28-L59).

Two Learning tables deserve focused follow-up: `learning_obligations`
and `queue_projection_diagnostics` have no direct own-runtime SQL match
in this scan. A V2 remediation regression explicitly expects the older
obligations/deliveries to remain empty for that scenario while the new
contribution is written. That makes them candidates for review, not
safe-drop conclusions. Dynamic exporters, views, triggers, old protocols
and retained history remain relevant. [V2 regression
source](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/services/learning/runtime/tests/test_remediation.py#L317-L339).

Database runtime/DDL totals 31,238 lines across supported source
languages; **22,389 (71.7%) belong to the legacy database owner**. This
is where the major code opportunity lies. Conversely, the seven
old-table names are only 4.3% of the legacy table count. A name-based
cleanup would offer little benefit and substantial risk.

The legacy database owner already has 279 runtime/DDL files, including
**120 files of 1–30 lines**. The desired unit should be one cohesive
capability, such as queue admission or publication persistence. One file
per SQL statement would make the navigation problem worse.

### How to establish actual usage

The prepared `database-runtime-census.sql` is read-only and has not been
executed. It collects actual objects, estimated row counts/sizes, access
counters and their reset time, active application names, foreign keys,
view dependencies and triggers. Run it against each identified
installation, then compare snapshots around representative Study use.
Counters can be disabled, reset or delayed; a zero count is not proof of
non-use. PostgreSQL documents these limits in its [statistics
documentation](https://www.postgresql.org/docs/current/monitoring-stats.html).
Dependency queries must distinguish catalog/object identity, as
documented for
[`pg_depend`](https://www.postgresql.org/docs/current/catalog-pg-depend.html).

The later table decision needs owner, active writer, direct and indirect
readers, retained history/import needs, recoverability, and the
surviving Study contract. Remove retired runtime code before considering
physical data drops. Keep code retirement and data migration separately
reviewable.

## 5. A simpler target organization

Preserve existing good component boundaries and the physical `database/`
home. Do not introduce a new service, package, command bus or abstract
repository for every logical module.

| Logical owner              | Retained responsibility                                                             | Suggested homes and dependency rules                                                                                                                                                                                 |
|----------------------------|-------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Study / Learning           | Execute saved content; select/issue/review cards; scheduling, teaching and recovery | Existing `services/learning` and Study frontend initially unchanged. Later coherent `queue`, `reviews`, `teaching`, `openings`, `http` modules. No required Games/Analysis/engine call or shared lifecycle.          |
| Content / authored Studies | Import/edit source lines or PGN, validate, publish executable definitions           | Existing `services/content`; use `sources`, `validation`, `publications`. Daily Study and authored Studies are different responsibilities. Publishing supplies saved definitions; it does not write Learning queues. |
| Games                      | Import actual games and compare them with supplied repertoire definitions           | Existing `services/game-evidence` and useful pure ingestion/comparison packages if retained. Emit facts or a semantic practice request; never write another owner's review/queue tables.                             |
| Analysis                   | Optional providers/engines and source-backed findings                               | Existing `services/intelligence` and workers only for explicitly selected useful features. Its failure cannot block Study.                                                                                           |
| Database                   | Owner-local connections, migrations and complete storage operations                 | Existing `database/tempo_<owner>_database`; group queries/adapters by a capability. Domain policy stays outside; connection/transaction authority remains singular.                                                  |
| Composition / operations   | Wire chosen implementations, routers, task capabilities and resources               | A small explicit bootstrap/worker layer. If legacy remains temporarily, this is the correct owner of cross-domain registration currently buried in `main.py` and the Games coordinator.                              |
| Shared pure code           | Chess identity/legal transformations and genuinely shared calculations              | Reuse existing chess/scheduling/math packages where they are actually consumed. No database, HTTP or startup effects; do not add shared infrastructure to save a tiny duplicate.                                     |

The design rule is **one reason to change per module**, not one function
per file. A 500-line cohesive workflow can be easier to understand than
twenty files that collectively implement it. Conversely, a 200-line
facade may still mix imports, migrations, leases, publication and
reporting.

## 6. Where DRY abstractions help

An exact-AST scan found 16 duplicate function groups of at least 20
physical lines. The first-copy-span metric gives 710 repeated lines; it
is not an exhaustive clone measure or guaranteed deletion total. The
separate database duplicate scan overlaps this evidence and must not be
added to it. Neither approaches a 34,843-line reduction.

Useful candidates are small pure reducers, the already repeated ranking
calculations, identical same-owner nonlocking reads, an exact cursor
codec, and explicit task metadata that replaces repeated lists.
Timing/error translation decorators and resource context managers can
help when they preserve clear control flow. `tasks.py` already has a
bounded maintenance decorator; absence of decorators is not the central
problem.

Avoid generic `@transactional`, `@retry` or publication frameworks
across owners. The current review effect, attempt receipt, scheduling,
queue completion and local settlement intentionally share a borrowed
transaction. A wrapper that commits each repository method or retries
with a new operation identity would shorten code while damaging
correctness and making review harder. Keep that ownership visible.

## 7. The proposed step-by-step reduction program

### Step 0 — Establish the running audit: completed for this pass

Pin source, inventory all tracked files and Python definitions,
reconstruct source-schema tables, separate inherited from fresh review,
and record the highest-value mixed modules. The included scripts can
regenerate this exact baseline without importing or executing Tempo. The
audit ledger is evidence for decisions, not an instruction to preload
the entire repository into every agent.

### Step 1 — Resolve the serving boundary with one read-only checkpoint

Record desktop/phone build target, API origin, actual
process/image/revision, database installation and migration version,
launcher, and whether current phone use depends on batch offline
preparation. Read the real deployment; do not infer it from `main` or a
diagram. Obtain the bounded database census. Keep Study running and
unchanged.

**Exit:** a list of protected paths/contracts/tables tied to the actual
deployment, with remaining uncertainties explicit. This is the immediate
next action that determines the size of the safe opportunity.

### Step 2 — Choose a small supported feature set

Recommended default for discussion: keep working daily Study and all
existing content/history; retain only the smallest source/import
capability needed to maintain those repertoires; preserve useful raw
game data without requiring an active Games pipeline. Put adaptive
discovery, overlapping rankings/coverage systems, extra analysis
variants, guided review, dashboards and optional generation up for
explicit keep-or-retire decisions. Do not treat this proposal as an
already approved removal of features Andy still wants.

For every feature, answer one question: which current user action
justifies keeping this implementation? “It might eventually be useful”
is a reason to preserve it in Git history, not automatically in the
supported serving tree.

### Step 3 — Retire one complete obsolete stack or feature slice

If Study is already independent, investigate retirement of the legacy
backend plus its exclusive database adapters, worker registrations,
Compose services, qualification tools and tests. That is the most
credible route to 50%. If Study is still legacy, begin only with feature
code outside its verified dependency chain; park cuts that require
changing Study.

For each batch list exact entrypoints, functions, adapters,
registrations, build inputs, tests and documentation removed; surviving
replacements; and any old-data reader retained. Remove obsolete source
from active main after approval; use Git history/a tag as the archive,
not a searchable `archive/` subtree. Do not first spend time beautifully
reorganizing a workflow chosen for retirement.

### Step 4 — Retire compatibility modes that no supported workflow needs

Confirm the SQLite support window and historical import requirements.
Where unused by the protected serving path, remove old runtime
initialization and dual-transport branches while keeping any required
converter as a bounded offline tool. Retiring SQLite serving does not
automatically retire SQLite-to-PostgreSQL query translation: the
retained Learning adapter still translates SQLite-shaped queries.
Changing those callers is a separate task. Preserve frozen-rank data;
distinguish generating rankings from consuming persisted ranks. Remove
old v1 protocol handling only after checking pending/historical callers
and receipts.

### Step 5 — Split only the surviving mixed modules

Use the ordered maps in this audit. Separate pure policy, application
workflows, transport and persistence when they have different reasons to
change. Keep one transaction and queue writer, explicit composition and
narrow public operations. Do not invent a framework for small
variations. Apply import-boundary checks only to affected owners; do not
create another universal qualification project.

### Step 6 — Reduce tables in separate later migrations

Review owner-qualified table families, including views, triggers,
export/import consumers and retained history. Select one authoritative
representation before consolidating projections. Preserve
data/export/rollback as required; source deletion is not permission to
drop stored history. A single SQL drop can be far more consequential
than thousands of deleted source lines.

### Step 7 — Measure the result and reduce agent entry cost

Report net implementation lines removed in both denominators, retired
runtime modes/job kinds, retained public interfaces, owning-table counts
and the actual source files needed for a routine task. Each surviving
owner should have a short README with purpose, public entry points,
owned state, one minimal test command and forbidden dependencies. Put
historical design discussions behind links; do not force them into
startup instructions. Use the generated lookup helper for one file or
symbol rather than loading the full inventories.

## 8. Acceptance for deletion batches

Use existing relevant tests plus a small fixture of the actual supported
Study composition. Check today's queue, next new move from each active
repertoire, expected frozen order/quotas, correct and wrong answers, one
durable review effect, wrong-answer repetition, reload, lost-response
replay, confirmed-review/failed-next-load recovery, current phone
offline/reconnect behavior and phone/computer conflicts. Stop optional
producers and verify Study continues. These are scoped checks of current
functionality, not a new whole-system testing framework.

Retire tests only with the behavior they exclusively tested. Preserve
named regressions for still-supported data/retry/progression invariants.
Moving a safety invariant requires identifying its new test owner, not
simply deleting the previous test to make a suite green.

The main counterargument is that some nonworking features contain
valuable algorithms and nearly complete independent implementations;
deleting selected packages could cost more than finishing one narrow
useful feature. Keep those only when a concrete near-term workflow
warrants them. The most likely failure mode of this program is spending
another large cycle reorganizing all existing code while continuing to
support every old path.

## 9. How to use the running inventory

The package includes an all-file index; a 597-file narrow
backend/database implementation inventory; a broader 881-file module
ledger; all 14,987 Python-definition spans; import edges; 292
source-table records; detailed substantive review records;
exact-duplicate evidence; the read-only census SQL; and
regeneration/lookup scripts.

Each module row records path, size, purpose and its evidence basis,
current owner, review depth/ranges, proposed action/destination, static
imports/callers, source revision and hash. “Mechanically catalogued”
means the file has not received a full semantic review. Inherited
repository proposals remain marked inherited until checked. “Candidate”
means investigate; it is not a deletion authorization.

The highest-value continuation is to resolve the deployed Study
identity, then close one complete retirement decision. The numeric
target is plausible, but this first pass has identified the opportunity
and dependencies—not certified 34,843 removable lines.

# Appendix A — Complete sequential main.py review

**Source:** `backend/app/main.py`, main commit
`7d77ed1b25198dcd320e0265f777cc630f23775e`.

**Review depth:** every numbered source line, 1–4,538, was read
sequentially. The companion `main-reviewed.json` records 40 bounded
responsibility ranges and all 187 definitions, each with its purpose,
source range, routes, PostgreSQL configuration checks, and literal
dispatched operations. An AST pass verified the inventory after the
semantic reading. This is a complete source read of this file, not a
claim that every execution path, imported implementation, or production
behavior was tested. No source, service, database, or deployment was
changed.

## Finding

`main.py` combines application assembly, request admission, two
storage/execution architectures, worker registration, and numerous
product workflows. It is not merely a long list of thin routes.
Splitting routes alone would make this file smaller while leaving the
system equally difficult to understand. The reduction must first decide
which complete capabilities survive, protect the working Study path, and
stop maintaining parallel execution paths where they are no longer
needed.

The static counts, verified against the complete source, are:

| Measure                                |                                              Count |
|----------------------------------------|---------------------------------------------------:|
| Physical lines                         |                                              4,538 |
| Function definitions                   |                       187: 184 top-level, 3 nested |
| Locally declared HTTP routes           |                                                141 |
| Included external routers              |                                                  9 |
| `postgres_store.configured()` calls    |                             92 across 90 functions |
| `dispatch_command(...)` calls          | 78, containing 72 distinct literal operation names |
| Import-time durable task registrations |                                                 17 |
| Import-time maintenance registrations  |                                                  3 |

These are source measurements, not production usage measurements. Route
counts exclude routes inside the nine included routers; definition
counts include exception handlers, middleware, lifecycle, helpers, and
nested callbacks. The counts and extraction method are in
`main-reviewed.json`.

## Ordered responsibility map

The following map follows the source from beginning to end. The JSON
ledger splits this into 40 contiguous ranges and supplies a
function-level index; grouped rows here keep the narrative readable.
“Retire candidate” requires product, dependency, and data/receipt
evidence. It never means safe to delete immediately.

| Lines     | Current functionality                                                                                                     | Initial disposition                                                                                      |
|-----------|---------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| 1–179     | Imports storage groups, service implementations, transport models, workers, and owner APIs.                               | Compose retained domains explicitly; remove imports with retired capabilities.                           |
| 180–289   | PostgreSQL/Celery versus SQLite/writer/coordinator lifecycle; FastAPI assembly; operation receipt read/retry; CORS.       | Keep startup separate from operation-recovery transport; verify actual runtime before removing a branch. |
| 290–420   | Legal game replay and analysis/PV/candidate validation; analysis threshold read.                                          | Games validation owner if retained; pure validation should not construct HTTP errors.                    |
| 421–807   | Manual route classification, cutover allowlist, foreground/background/query-only contexts, two timing selections.         | One explicit request policy per retained route, with current exceptions preserved.                       |
| 808–1007  | Error mapping, health, activity, tasks, diagnostics, worker progress/control/retry.                                       | Operations transport; retain Study resource-priority and receipt/error behavior.                         |
| 1008–1107 | Capabilities, general settings, provider accounts, queue/coverage/discovery/policy refresh fanout.                        | Separate settings by owner and make consequential refreshes explicit.                                    |
| 1108–1292 | Queue compatibility facade; queue task execution; seventeen task and three maintenance registrations; refresh scheduling. | Migrate reverse imports; separate worker bootstrap from HTTP; protect queue maintenance.                 |
| 1293–1408 | Queue payload, game encounter badges, exercise snapshots, and durable head issuance; today/prepared/window routes.        | Protect this whole observed Study workflow before removing optional decoration.                          |
| 1409–1575 | PGN discard/import, segmentation, source/card persistence, annotations, deduplication, dependent rebuilds.                | Repertoire ingestion owner; decide whether import remains in MVP.                                        |
| 1576–1709 | Repertoire list/lines/membership/comparison; integrity summary and repair submission; FEN normalization.                  | Preserve Study source reads/blockers; separate comparisons and repairs.                                  |
| 1710–1846 | Annotations; per-card teaching state; portable snapshot and checksum.                                                     | Three owners: repertoire content, Study teaching, operations export.                                     |
| 1847–1991 | Main repertoire/deletion; queue state/fail/bury; review settlement and reconciliation.                                    | Protect selected repertoire, admission ordering, attempts, receipts, and Learning adapter behavior.      |
| 1992–2192 | Pasted-analysis preview/commit; branch add/remove; graph/integrity/coverage refresh.                                      | Repertoire authoring application operations, with explicit required follow-up work.                      |
| 2193–2474 | Progress; legal line validation; prefix split; card revision/history migration; archive/deletion.                         | Split pure validation from stateful mutation; protect tombstones/history/revision guards.                |
| 2475–2578 | Repertoire limits/rename; annotated PGN export.                                                                           | Repertoire settings and serialization; preserve new-card policy and content portability.                 |
| 2579–2807 | Coverage/statistics/opportunity reads; discovery feed, recommendations, durable admission and curation.                   | Optional capability retirement candidate; preserve admitted source/cards and outstanding receipts.       |
| 2808–2950 | Maia availability/leases/results, Explorer credentials and HTTP proxy.                                                    | Retire with the unused provider/coverage chain or choose one retained provider authority.                |
| 2951–3113 | Tactic motif catalog, activation, captures, attempt settlement, progress.                                                 | Separate generation/catalog from existing exercise settlement.                                           |
| 3114–3160 | Retired endgame computation, saved-template reads, and recovery of prior attempt receipts.                                | Compatibility cleanup only after consumers and receipt obligations are resolved.                         |
| 3161–3585 | Game accounts/sync/status/profile; parent and position engine leases, callbacks, finalization, repairs.                   | Games ingestion/analysis owners if retained; substantial optional chain candidate.                       |
| 3586–3726 | Validated analysis publication, supersession, completion, then derivation/threat/discovery fanout.                        | One transaction-owning Games publication operation; retire with its full producer chain.                 |
| 3727–4078 | Defensive engine controls/reports/backfill, candidate curation/admission, exercise delivery and settlement.               | Separate optional producers from already admitted Study exercises.                                       |
| 4079–4427 | Game findings/curation/adaptation/card creation; game DTOs, pagination and position statistics.                           | Games product retirement candidate; existing created cards and priority facts require preservation.      |
| 4428–4538 | Statistics/insights; guided-review sessions/attempts; seven more opening/prefix routers.                                  | Optional product owners plus a compact explicit router list; protect mounted Study evidence routes.      |

Source for the complete ordered map: [pinned
main.py](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py).
Exact per-function citations are attached to every ledger entry.

## Three causes of disproportionate complexity

### 1. A second router is embedded inside middleware

The decorated middleware spans **361 physical lines, 421–781**. It
independently parses URL segments and HTTP verbs, defines dozens of
booleans, and enumerates a large PostgreSQL write allowlist. FastAPI
route declarations subsequently describe those paths again. Separate
branches classify read-only POSTs, background work, GETs that can mutate
state, and paths receiving timing logs. Another middleware repeats path
selection for response timing. A route change therefore has policy
consequences outside its own handler. [Request classification and
admission](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L421-L805).

An appropriate DRY abstraction is a small, explicit route policy
recording work class, mutation semantics, and timing label alongside
each route. One route wrapper or dependency can enforce those policies.
FastAPI middleware runs before normal route handling, so replacing the
current mechanism with a lookup of an unavailable route object would be
incorrect; choose the integration point deliberately. Preserve
background delivery exemptions, foreground admission boundaries, and
context propagation. This is a focused abstraction opportunity, not a
justification for a new generic framework.

The unusual cases are real: queue GETs issue durable attempts,
guided-review GETs reconcile sessions, and specific callbacks avoid
acquiring a foreground lease that would wait on itself. A generic “GET
means read-only” decorator would change behavior. [Explicit
exemptions](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L728-L769).

### 2. Storage selection changes the workflow, not just SQL syntax

The 92 PostgreSQL configuration checks are a symptom of two execution
architectures. Startup either dispatches queue work through durable
commands and publishes priority, or initializes a local writer plus
embedded/external coordinator. Many routes prepare a PostgreSQL command
but implement the SQLite transaction directly in this file.
[Lifecycle](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L180-L232);
[PGN
import](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1423-L1573).

`save_game_analysis` is particularly clear: its PostgreSQL path
validates/classifies and admits a durable command; its SQLite path
reconstructs rows, validates a lease again inside the write boundary,
replaces analysis/candidates, marks supersession, completes the job, and
starts three other kinds of work. This is duplicated orchestration,
beyond the choice of database driver. [Analysis
publication](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3586-L3724).

After identifying the actual serving Study deployment, select the
required storage/runtime model. Removing an unsupported execution branch
can eliminate implementation and its dedicated tests together. Wrapping
both branches in a polymorphic abstraction would preserve the
maintenance burden unless both are real requirements. If both must
remain, use one named application operation and keep transaction
ownership explicit; a decorator must not silently retry writes or
swallow uncertain outcomes.

### 3. Importing HTTP also composes worker and business behavior

The file registers 17 durable task handlers and three maintenance
callbacks at import time. `background_worker.py` explicitly imports
`main.coordinator` to obtain those registrations. `study_routes.py`
imports `main.enqueue_daily_queue_refresh`; `canonical_prefix_api.py`
imports `main.coordinator`. This is a genuine reverse dependency from
workers/routers into the entrypoint.
[Registrations](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1199-L1290);
[worker
import](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/background_worker.py#L12);
[Study router
import](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/study_routes.py#L77);
[prefix router
import](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/canonical_prefix_api.py#L97-L121).

The same module contains substantial application workflows: settings
trigger several domain refreshes; importing/pasting/branching source
repeats graph/integrity/coverage scheduling; card revision migrates
references/history; analysis publication starts derivation, defense, and
discovery. Moving each route into a different file would not remove
these responsibilities. Use an explicit worker-registration composition
function, owner-level commands for the workflows, and transport code
that only validates the transport shape and calls a public operation.
Consolidate source-change refresh only where the required effects and
error behavior are actually the same.
[Settings](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1020-L1105);
[source
authoring](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1992-L2190);
[card
revision](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2339-L2429).

## Protect, split, or retire

**Protect first:** queue delivery/issuance, review/reconciliation,
teaching state, selected repertoire and per-repertoire new-card limits,
required source reads, deletion tombstones/history, and current
installed exercise formats. `_queue_payload` reads game encounter badges
before issuing the head, so removing “Games” tables or modules by name
could break a Study response even when Games itself is not useful.
Review handlers delegate into Learning and carry explicit
attempt/checkpoint identity. These edges must be measured and separated
before deletion. [Queue payload and
issuance](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1293-L1406);
[review
settlement](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1878-L1989).

**Split retained work:** bootstrap, operations, Study
queue/review/teaching, repertoire content/authoring, and only the
optional domain capabilities explicitly retained. Reuse existing owner
modules and public APIs. Destination names in the JSON express
responsibility; they are not an instruction to create another parallel
organization tree. Each retained domain should expose a small public
surface; persistence belongs to its established owner. Pure PGN
serialization and legal-line validation can be independent of HTTP,
while history migrations and receipt settlement remain explicit
transaction-owning operations.

**Investigate retirement as complete chains:** unsupported Games
ingestion/analysis/adaptation/guided reviews,
discovery/coverage/provider enrichment, statistics, and optional
exercise generators. Distinguish producing future exercises from serving
exercises already saved. The endgame routes demonstrate why: computation
returns 410, but the attempt route still returns prior successful or
failed operation results and pending receipts before rejecting new work.
Saved templates also remain readable. [Endgame
compatibility](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3114-L3158).

The 91-line queue compatibility band at 1108–1198 is a smaller cleanup
candidate: most functions delegate to actual queue owners. Remove it
after migrating imports and tests that patch this facade. Moving 4,538
lines to other files does **not** count as code reduction. Neither this
review nor static reachability establishes that half of this file is
safe to delete today.

## Concrete next sequence

1.  Establish which phone/desktop build, API origin, process entrypoint,
    storage backend, and queue/review contracts currently deliver
    working Study. Record the protected boundary before changing routing
    or data ownership.
2.  Choose one nonessential capability chain, inventory its
    producer/worker/routes/client/tables and incoming Study edges, then
    remove or deliberately replace those edges. Preserve saved source,
    exercises, facts, and receipts independently of the retired
    producer.
3.  Move only retained startup and job registrations into explicit
    composition. Stop requiring worker or sibling-router imports from
    `main`; migrate the queue facade's callers to its existing owner.
4.  Retire the unused storage/runtime branch if deployment evidence
    permits. Carry the required workflow and idempotency behavior into
    its single owner instead of preserving two hidden paths.
5.  Replace repeated route-policy classification with one bounded
    declaration per retained route. Validate the concrete risky cases:
    queue GET issuance, repeated review delivery, background callback
    admission, and deleted-card rejection.
6.  Move remaining business workflows into their logical owners and
    leave the entrypoint assembling them. Measure net removed production
    lines and retired interfaces; record moved lines separately.
    Continue this ledger through dependency modules rather than claiming
    the rest of the repository has already received a complete source
    read.

No live route usage, database contents, or serving deployment identity
was established by this file review. Those are the remaining evidence
gaps that govern deletion, not reasons to delay the read-only
organization work already completed.

## Appendix B — Every function in main.py

All 187 definitions below were individually described after the complete
sequential source read. Routes, PostgreSQL branch checks and dispatched
operation names for each definition are in `main-reviewed.json` in the
evidence package.

Open the complete 187-function map

| Definition                                    | Lines                                                                                                                                | Responsibility             | Purpose                                                                                                                                        |
|-----------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|----------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| `lifespan`                                    | [181–232](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L181-L232)     | lifecycle                  | Initialize selected storage/runtime mode, ensure queue, publish Study priority, and clean up the SQLite writer/coordinator.                    |
| `review_conflict_response`                    | [243–245](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L243-L245)     | app_and_receipts           | Turn ReviewConflict into its structured 409 response.                                                                                          |
| `operation_status`                            | [249–256](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L249-L256)     | app_and_receipts           | Read a PostgreSQL command receipt or report missing/unsupported operation.                                                                     |
| `retry_blocked_operation`                     | [260–283](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L260-L283)     | app_and_receipts           | Retry an existing blocked command through Celery while retaining operation identity and retry cycle.                                           |
| `_analysis_error`                             | [295–298](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L295-L298)     | analysis_validation        | Construct a structured invalid-analysis exception tied to the submitted ply.                                                                   |
| `_validate_analysis_line`                     | [301–314](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L301-L314)     | analysis_validation        | Replay a principal variation from a position and reject illegal moves.                                                                         |
| `_validated_analysis_evaluations`             | [317–410](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L317-L410)     | analysis_validation        | Reconstruct game positions and validate/normalize each evaluation and candidate line.                                                          |
| `_game_analysis_threshold`                    | [413–418](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L413-L418)     | analysis_validation        | Read the analysis-loss threshold through background storage admission.                                                                         |
| `prioritize_foreground_requests`              | [422–781](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L422-L781)     | request_policy             | Classify path/method, enforce PostgreSQL write-route cutover, choose query-only/background/foreground context, and time selected requests.     |
| `measure_training_request`                    | [785–805](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L785-L805)     | timing                     | Attach request timing, SQL-count and transaction-count headers for selected training paths.                                                    |
| `background_waiting`                          | [809–811](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L809-L811)     | error_mapping              | Map deferred background admission to retryable HTTP 503.                                                                                       |
| `deleted_content_conflict`                    | [815–818](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L815-L818)     | error_mapping              | Map the specific deleted-card integrity violation to HTTP 409 and rethrow other integrity errors.                                              |
| `storage_unavailable`                         | [822–845](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L822-L845)     | error_mapping              | Map SQLite busy/locked failures to structured retryable storage errors with diagnostic logging.                                                |
| `health`                                      | [849–871](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L849-L871)     | operations                 | Return PostgreSQL readiness/frozen-order state or SQLite writer/settings health.                                                               |
| `foreground_active`                           | [875–878](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L875-L878)     | operations                 | Report the foreground-demand snapshot used to yield background work.                                                                           |
| `foreground_requests_active`                  | [882–884](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L882-L884)     | operations                 | Report active foreground request leases.                                                                                                       |
| `record_browser_activity`                     | [888–891](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L888-L891)     | operations                 | Record browser activity into the Study-priority service.                                                                                       |
| `system_tasks`                                | [895–922](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L895-L922)     | operations                 | Assemble background jobs, projections, and writer health for operations UI.                                                                    |
| `system_background_diagnostics`               | [926–928](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L926-L928)     | operations                 | Return scheduler/background diagnostic state.                                                                                                  |
| `system_activity`                             | [932–937](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L932-L937)     | operations                 | Read operation activity rows and decode their payloads.                                                                                        |
| `control_system_activity`                     | [941–957](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L941-L957)     | operations                 | Validate activity-control action, dispatch in PostgreSQL, or mutate local controls and wake the coordinator.                                   |
| `report_system_activity_progress`             | [961–988](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L961-L988)     | operations                 | Validate game-analysis progress callback and dispatch or persist against its lease.                                                            |
| `retry_system_task`                           | [992–1005](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L992-L1005)   | operations                 | Retry a durable task through the selected runtime, clear pause state, and wake the scheduler.                                                  |
| `capabilities`                                | [1009–1010](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1009-L1010) | settings                   | Expose configured analysis/provider capabilities.                                                                                              |
| `get_settings`                                | [1014–1017](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1014-L1017) | settings                   | Read persisted application settings as transport Settings.                                                                                     |
| `put_settings`                                | [1021–1105](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1021-L1105) | settings                   | Persist many owners' settings/accounts and trigger affected queue, coverage, discovery and priority refreshes.                                 |
| `put_settings.persist_settings`               | [1037–1081](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1037-L1081) | settings                   | Nested SQLite write callback for settings and provider-account upserts/deletions.                                                              |
| `reconcile_unseen_queue`                      | [1108–1109](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1108-L1109) | queue_compatibility        | Compatibility delegate for queue reconciliation.                                                                                               |
| `_plan_prioritized_opening_admissions`        | [1117–1122](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1117-L1122) | queue_compatibility        | Compatibility delegate for prioritized opening admission planning.                                                                             |
| `admit_prioritized_opening_cards`             | [1125–1126](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1125-L1126) | queue_compatibility        | Compatibility delegate for applying planned opening admissions.                                                                                |
| `_unlock_eligible_opening_cards`              | [1139–1142](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1139-L1142) | queue_compatibility        | Compatibility delegate for eligibility unlocking.                                                                                              |
| `_block_ineligible_opening_queue_entries`     | [1145–1146](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1145-L1146) | queue_compatibility        | Compatibility delegate for blocking ineligible opening entries.                                                                                |
| `_block_unapproved_defense_queue_entries`     | [1149–1150](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1149-L1150) | queue_compatibility        | Compatibility delegate for blocking unapproved defense entries.                                                                                |
| `_restore_eligible_due_queue_entries`         | [1153–1154](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1153-L1154) | queue_compatibility        | Compatibility delegate for restoring due entries.                                                                                              |
| `_restore_published_study_queue_entries`      | [1157–1158](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1157-L1158) | queue_compatibility        | Compatibility delegate for restoring published Study entries.                                                                                  |
| `_reset_unintroduced_opening_cards`           | [1164–1165](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1164-L1165) | queue_compatibility        | Compatibility delegate for unintroduced-card reset.                                                                                            |
| `_reset_stale_opening_introductions`          | [1168–1169](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1168-L1169) | queue_compatibility        | Compatibility delegate for stale introduction reset.                                                                                           |
| `seed_queue`                                  | [1172–1173](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1172-L1173) | queue_compatibility        | Compatibility delegate for daily queue seeding.                                                                                                |
| `_queue_membership_hash`                      | [1179–1180](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1179-L1180) | queue_compatibility        | Compatibility delegate for queue-membership hashing.                                                                                           |
| `_plan_daily_queue_order`                     | [1183–1184](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1183-L1184) | queue_compatibility        | Compatibility delegate for deterministic queue ordering.                                                                                       |
| `randomize_daily_queue`                       | [1187–1188](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1187-L1188) | queue_compatibility        | Compatibility delegate for applying daily queue order.                                                                                         |
| `_quarantine_malformed_opening_cards`         | [1191–1192](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1191-L1192) | queue_compatibility        | Compatibility delegate for invalid opening-card quarantine.                                                                                    |
| `materialize_daily_queue`                     | [1195–1196](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1195-L1196) | queue_compatibility        | Compatibility delegate for materializing the daily projection.                                                                                 |
| `_execute_daily_queue_task`                   | [1199–1213](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1199-L1213) | job_composition            | Run queue materialization through the background writer and persist failed projection state before rethrowing.                                 |
| `_ensure_current_daily_queue`                 | [1238–1243](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1238-L1243) | job_composition            | Maintenance callback to enqueue a missing daily queue projection.                                                                              |
| `_ensure_daily_defense_admission`             | [1249–1259](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1249-L1259) | job_composition            | Maintenance callback to enqueue eligible daily defense admissions when analysis is enabled.                                                    |
| `_ensure_discovery_admissions`                | [1265–1269](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1265-L1269) | job_composition            | Maintenance callback to resume a pending discovery-admission intent.                                                                           |
| `enqueue_daily_queue_refresh`                 | [1275–1290](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1275-L1290) | job_composition            | Enqueue queue work, mark the projection refreshing through the correct writer class, and wake the coordinator.                                 |
| `_issue_returned_queue_head`                  | [1293–1310](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1293-L1310) | queue_reads                | Recover or durably issue the first delivered queue attempt through dispatch/local issuance.                                                    |
| `_queue_payload`                              | [1313–1374](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1313-L1374) | queue_reads                | Build queue cards/projection/diagnostics and encounter badges, then issue the returned head.                                                   |
| `queue_today`                                 | [1378–1379](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1378-L1379) | queue_reads                | Return the full current daily queue payload.                                                                                                   |
| `prepared_queue`                              | [1383–1399](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1383-L1399) | queue_reads                | Return queue payload enriched with exercise revision and rendering snapshots.                                                                  |
| `queue_window`                                | [1403–1406](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1403-L1406) | queue_reads                | Validate a bounded window size and return that many queue cards.                                                                               |
| `discard_pending_pgn`                         | [1410–1420](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1410-L1420) | pgn_import                 | Dispatch idempotent discard/retry for a pending PostgreSQL import operation.                                                                   |
| `import_pgn`                                  | [1424–1573](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1424-L1573) | pgn_import                 | Parse uploaded PGN, prepare PostgreSQL command or persist segmented SQLite source/cards, then schedule dependent rebuilds.                     |
| `list_repertoires`                            | [1577–1592](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1577-L1592) | repertoire_reads_integrity | List repertoire counts with priority/prefix projection status.                                                                                 |
| `repertoire_move_membership`                  | [1596–1601](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1596-L1601) | repertoire_reads_integrity | Read whether a legal move belongs to a saved repertoire.                                                                                       |
| `repertoire_lines`                            | [1605–1610](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1605-L1610) | repertoire_reads_integrity | Return saved repertoire source lines for the requested position/source.                                                                        |
| `comparison_cards`                            | [1614–1631](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1614-L1631) | repertoire_reads_integrity | Build comparison cards and repertoire membership from stored source rows.                                                                      |
| `repertoire_conflicts`                        | [1635–1637](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1635-L1637) | repertoire_reads_integrity | Return stored repertoire conflict rows.                                                                                                        |
| `repertoire_integrity`                        | [1641–1648](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1641-L1648) | repertoire_reads_integrity | Read integrity summary and issues for a repertoire.                                                                                            |
| `resolve_repertoire_integrity`                | [1652–1700](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1652-L1700) | repertoire_reads_integrity | Prepare/dispatch an integrity resolution or enqueue a signature-checked repair task.                                                           |
| `fen_key`                                     | [1703–1707](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1703-L1707) | repertoire_reads_integrity | Validate and canonicalize a FEN for position lookups.                                                                                          |
| `list_annotations`                            | [1711–1731](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1711-L1731) | annotations                | Read annotation rows and decode comments/arrows/squares for transport.                                                                         |
| `save_annotation`                             | [1735–1762](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1735-L1762) | annotations                | Dispatch annotation change or upsert/delete its SQLite representation.                                                                         |
| `list_teaching_states`                        | [1766–1779](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1766-L1779) | teaching                   | Read per-card teaching positions as transport state.                                                                                           |
| `mark_teaching_state`                         | [1783–1802](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1783-L1802) | teaching                   | Validate active card and persist/dispatch teaching state.                                                                                      |
| `migration_snapshot`                          | [1806–1844](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1806-L1844) | snapshot                   | Export portable source tables with stable JSON checksum, excluding automatic enrichment.                                                       |
| `make_main_repertoire`                        | [1848–1860](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1848-L1860) | repertoire_lifecycle       | Change selected repertoire and refresh queue-related game scope.                                                                               |
| `delete_repertoire`                           | [1864–1871](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1864-L1871) | repertoire_lifecycle       | Dispatch or delegate deletion to the repertoire deletion owner.                                                                                |
| `requeue`                                     | [1874–1875](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1874-L1875) | queue_actions              | Compatibility delegate for queue requeue semantics.                                                                                            |
| `queue_entry_state`                           | [1879–1890](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1879-L1890) | queue_actions              | Return entry day/status and current queue-head status for reconciliation.                                                                      |
| `mark_attempt_failed`                         | [1894–1907](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1894-L1907) | queue_actions              | Validate attempt identity then persist/dispatch a failed queue attempt.                                                                        |
| `bury_queue_entry`                            | [1911–1927](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1911-L1927) | queue_actions              | Validate attempt identity and retain the buried queue entry with its ordering/admission context.                                               |
| `review`                                      | [1931–1951](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1931-L1951) | reviews                    | Prepare durable PostgreSQL review/checkpoint input or call local Learning settlement.                                                          |
| `reconcile_review`                            | [1955–1972](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1955-L1972) | reviews                    | Require queue attempt identities and reconcile a durable review receipt through selected runtime.                                              |
| `_reconcile_review`                           | [1975–1981](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1975-L1981) | reviews                    | Call review reconciliation while translating conflicts into structured transport errors.                                                       |
| `_apply_review`                               | [1984–1989](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1984-L1989) | reviews                    | Delegate to installed Learning through legacy adapter with observation time, queue day, and settings policy.                                   |
| `preview_analysis_paste`                      | [1993–2000](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L1993-L2000) | source_authoring           | Preview parsed analysis and translate source-validation errors to HTTP.                                                                        |
| `commit_analysis_paste`                       | [2004–2060](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2004-L2060) | source_authoring           | Recover/prepare a command or commit parsed source locally, then queue graph/integrity/coverage refreshes.                                      |
| `branch`                                      | [2064–2124](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2064-L2124) | source_authoring           | Validate/add source branch and canonical route, resolve linked gaps, and trigger dependent rebuilds.                                           |
| `remove_branch`                               | [2128–2190](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2128-L2190) | source_authoring           | Match/remove source lines by move prefix and refresh source-derived projections.                                                               |
| `progress_summary`                            | [2194–2216](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2194-L2216) | progress                   | Aggregate card, recent review, queue, and blocker counts for Study progress.                                                                   |
| `validated_line`                              | [2219–2238](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2219-L2238) | card_authoring             | Validate nonempty legal UCI sequence and starting position.                                                                                    |
| `validate_card`                               | [2242–2248](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2242-L2248) | card_authoring             | Return a canonical card identity for a validated line.                                                                                         |
| `prefix_split_preview`                        | [2252–2265](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2252-L2265) | card_authoring             | Preview line prefix splitting and shared usage.                                                                                                |
| `prefix_split_accept`                         | [2269–2312](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2269-L2312) | card_authoring             | Dispatch or apply split with revision/usage guards, integrity validation, and graph tasks in its transaction.                                  |
| `prefix_split_reject`                         | [2316–2336](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2316-L2336) | card_authoring             | Persist the rejected prefix offer against revision/latest failure review.                                                                      |
| `revise_card`                                 | [2340–2429](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2340-L2429) | card_authoring             | Revise legal source, migrate/reset card identity/history/queue membership, preserve tombstones, and schedule integrity work.                   |
| `preview_card_deletion`                       | [2433–2436](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2433-L2436) | card_authoring             | Return shared-use and deletion consequences from deletion owner.                                                                               |
| `archive_card`                                | [2440–2472](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2440-L2472) | card_authoring             | Archive or permanently delete with revision guards and queue/integrity updates.                                                                |
| `update_repertoire_settings`                  | [2476–2491](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2476-L2491) | repertoire_metadata        | Change repertoire new-card limit and request queue refresh.                                                                                    |
| `rename_repertoire`                           | [2495–2506](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2495-L2506) | repertoire_metadata        | Change repertoire display name through dispatch or local persistence.                                                                          |
| `export_repertoires`                          | [2509–2566](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2509-L2566) | pgn_export                 | Read source/annotations and build annotated PGN download for one or all repertoires.                                                           |
| `export_repertoires.apply_annotation`         | [2518–2545](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2518-L2545) | pgn_export                 | Nested PGN formatter for annotation comments, arrows, and squares.                                                                             |
| `export_one`                                  | [2570–2571](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2570-L2571) | pgn_export                 | Expose one repertoire's PGN export.                                                                                                            |
| `export_all`                                  | [2575–2576](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2575-L2576) | pgn_export                 | Expose all repertoires' PGN export.                                                                                                            |
| `refresh_repertoire_coverage`                 | [2580–2593](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2580-L2593) | coverage_reads             | Dispatch or enqueue coverage refresh and wake the coordinator.                                                                                 |
| `repertoire_statistics_summary`               | [2597–2603](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2597-L2603) | coverage_reads             | Return repertoire statistics for a validated window.                                                                                           |
| `repertoire_statistics_positions`             | [2607–2614](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2607-L2614) | coverage_reads             | Return position-level repertoire statistics for filters/window.                                                                                |
| `repertoire_coverage`                         | [2618–2619](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2618-L2619) | coverage_reads             | Read coverage summary projection.                                                                                                              |
| `repertoire_coverage_gaps`                    | [2623–2624](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2623-L2624) | coverage_reads             | Read uncovered repertoire continuation positions.                                                                                              |
| `repertoire_opportunities`                    | [2628–2632](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2628-L2632) | coverage_reads             | Read/filter repertoire opportunities with state validation.                                                                                    |
| `discoveries_feed`                            | [2636–2650](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2636-L2650) | discovery                  | Page grouped discovery opportunities and attach selected detail.                                                                               |
| `discovery_recommendations`                   | [2654–2660](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2654-L2660) | discovery                  | Read recommendations for an opportunity and map unknown opportunity.                                                                           |
| `accept_discovery_continuation`               | [2664–2690](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2664-L2690) | discovery                  | Prepare/dispatch acceptance or create a durable admission intent and wake its worker.                                                          |
| `discovery_admission_status`                  | [2694–2704](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2694-L2704) | discovery                  | Combine admission intent state with failed/preparing task status.                                                                              |
| `refresh_repertoire_opportunities`            | [2708–2721](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2708-L2721) | discovery                  | Request opportunity refresh through dispatch or background scheduler.                                                                          |
| `dismiss_repertoire_opportunity`              | [2725–2739](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2725-L2739) | discovery                  | Dispatch or persist dismissed opportunity state.                                                                                               |
| `acknowledge_repertoire_opportunity`          | [2743–2757](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2743-L2757) | discovery                  | Dispatch or persist acknowledged opportunity state.                                                                                            |
| `snooze_repertoire_opportunity`               | [2761–2775](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2761-L2775) | discovery                  | Dispatch or persist snoozed opportunity state.                                                                                                 |
| `repertoire_opportunity_training_eligibility` | [2779–2784](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2779-L2784) | discovery                  | Return whether an opportunity can be admitted to training.                                                                                     |
| `train_repertoire_opportunity`                | [2788–2805](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2788-L2805) | discovery                  | Dispatch or perform explicit opportunity training admission.                                                                                   |
| `coverage_maia_available`                     | [2809–2818](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2809-L2818) | providers                  | Gate provider work by Study-only mode and expose PostgreSQL worker availability.                                                               |
| `coverage_maia_claim`                         | [2822–2827](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2822-L2827) | providers                  | Claim Maia coverage inference work through its owner.                                                                                          |
| `coverage_explorer_session`                   | [2831–2839](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2831-L2839) | providers                  | Register Explorer session credential and wake provider work.                                                                                   |
| `coverage_maia_submit`                        | [2843–2857](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2843-L2857) | providers                  | Submit worker coverage results through command dispatch or local owner.                                                                        |
| `coverage_maia_heartbeat`                     | [2861–2875](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2861-L2875) | providers                  | Renew a coverage inference lease through selected runtime.                                                                                     |
| `coverage_maia_release`                       | [2879–2891](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2879-L2891) | providers                  | Release a coverage inference lease through selected runtime.                                                                                   |
| `coverage_maia_failure`                       | [2895–2916](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2895-L2916) | providers                  | Record coverage inference failure and trigger opportunity refresh where required.                                                              |
| `explorer`                                    | [2920–2948](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2920-L2948) | providers                  | Proxy a validated Lichess/Masters Explorer request with optional session authorization and upstream error mapping.                             |
| `tactics_catalog`                             | [2969–2971](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2969-L2971) | tactics                    | Return the tactic-pack catalog/projection.                                                                                                     |
| `tactics_activation`                          | [2975–2994](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2975-L2994) | tactics                    | Change pack activation, return catalog state, and refresh queue.                                                                               |
| `tactics_activation.persist_activation`       | [2985–2990](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2985-L2990) | tactics                    | Nested SQLite callback to persist tactics activation and read the resulting catalog.                                                           |
| `create_tactic_capture`                       | [2998–3013](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L2998-L3013) | tactics                    | Validate capture idempotency identity, persist captured tactic, and refresh queue.                                                             |
| `tactic_attempt`                              | [3017–3075](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3017-L3075) | tactics                    | Validate pack/puzzle, recover prior receipt, settle light/normal tactic state, and possibly requeue guided failures.                           |
| `tactic_progress`                             | [3079–3111](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3079-L3111) | tactics                    | Aggregate tactic progress while normalizing legacy/canonical pack identifiers.                                                                 |
| `probe_endgame`                               | [3115–3116](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3115-L3116) | endgames                   | Reject retired endgame probing with HTTP 410.                                                                                                  |
| `list_endgames`                               | [3120–3123](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3120-L3123) | endgames                   | Return saved endgame templates and decoded moves.                                                                                              |
| `create_endgame`                              | [3127–3129](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3127-L3129) | endgames                   | Reject retired endgame template generation with HTTP 410.                                                                                      |
| `create_endgame_attempt`                      | [3133–3158](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3133-L3158) | endgames                   | Recover a prior PostgreSQL attempt receipt before rejecting new retired endgame computation.                                                   |
| `accounts`                                    | [3162–3172](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3162-L3172) | games_ingestion            | Dispatch game-provider account changes or route SQLite updates through general settings.                                                       |
| `sync`                                        | [3176–3206](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3176-L3206) | games_ingestion            | Normalize provider accounts, request synchronization, and return queued/status state.                                                          |
| `next_opponent_profile`                       | [3210–3220](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3210-L3220) | games_ingestion            | Expose the PostgreSQL next-opponent profile and reject unsupported SQLite behavior.                                                            |
| `sync_status`                                 | [3224–3242](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3224-L3242) | games_ingestion            | Assemble configured provider accounts and latest synchronization job state.                                                                    |
| `claim_game_analysis`                         | [3246–3263](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3246-L3263) | analysis_leases            | Require recognized Docker worker behavior, expose no job to old browser clients, and claim through selected runtime.                           |
| `_claim_game_analysis`                        | [3266–3293](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3266-L3293) | analysis_leases            | Schedule ordinary analysis, expire leases, claim a queued parent job, and record fairness/activity.                                            |
| `_require_docker_engine`                      | [3296–3298](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3296-L3298) | analysis_leases            | Reject worker calls without the Docker-engine marker.                                                                                          |
| `claim_game_analysis_position`                | [3302–3342](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3302-L3342) | analysis_leases            | Recover an existing claim receipt, prepare parent/position lease, and honor Study-only mode.                                                   |
| `submit_game_analysis_position`               | [3346–3368](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3346-L3368) | analysis_leases            | Validate submitted position report then persist or dispatch it against its lease.                                                              |
| `release_game_analysis_position`              | [3372–3385](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3372-L3385) | analysis_leases            | Release position-level engine work through selected runtime.                                                                                   |
| `fail_game_analysis_position`                 | [3389–3402](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3389-L3402) | analysis_leases            | Record position-level engine failure through selected runtime.                                                                                 |
| `finalize_game_analysis_position`             | [3406–3452](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3406-L3452) | analysis_leases            | Build complete game evaluations from leased position evidence and admit final publication.                                                     |
| `repair_one_stockfish_timeout`                | [3456–3479](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3456-L3479) | analysis_leases            | Requeue one timed-out engine analysis with current provenance.                                                                                 |
| `repair_one_legacy_network_identity`          | [3483–3508](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3483-L3508) | analysis_leases            | Requeue one analysis whose saved network identity differs from verified worker provenance.                                                     |
| `fail_game_analysis`                          | [3512–3529](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3512-L3529) | analysis_leases            | Validate parent lease and persist/dispatch failed analysis job state.                                                                          |
| `heartbeat_game_analysis`                     | [3533–3549](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3533-L3549) | analysis_leases            | Renew parent analysis lease or reject stale lease.                                                                                             |
| `release_game_analysis`                       | [3553–3565](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3553-L3565) | analysis_leases            | Release parent analysis lease and return queued/stale state.                                                                                   |
| `retry_game_analysis`                         | [3569–3583](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3569-L3583) | analysis_leases            | Retry a failed parent analysis job and resume its activity controls.                                                                           |
| `save_game_analysis`                          | [3587–3724](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3587-L3724) | analysis_publication       | Validate/classify submitted evaluations, recover receipt, atomically publish analysis/candidates and completion, then enqueue downstream work. |
| `defensive_engine_control`                    | [3728–3743](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3728-L3743) | defense_producers          | Read authoritative lease policy while immediately yielding to foreground/Study-only demand.                                                    |
| `claim_defensive_threat_analysis`             | [3747–3760](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3747-L3760) | defense_producers          | Require Docker worker and claim a defensive engine request.                                                                                    |
| `audit_defensive_threat_reports`              | [3764–3777](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3764-L3777) | defense_producers          | Dispatch or schedule an audit of saved threat reports.                                                                                         |
| `backfill_defensive_threats`                  | [3781–3792](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3781-L3792) | defense_producers          | Dispatch or enqueue threat backfill work.                                                                                                      |
| `submit_defensive_threat_analysis`            | [3796–3819](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3796-L3819) | defense_producers          | Validate/persist threat analysis report and enqueue candidate validation.                                                                      |
| `fail_defensive_threat_analysis`              | [3823–3845](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3823-L3845) | defense_producers          | Record request failure against active lease and return resulting retry state.                                                                  |
| `release_defensive_threat_analysis`           | [3849–3863](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3849-L3863) | defense_producers          | Release request lease and return queued/stale state.                                                                                           |
| `retry_defensive_threat_analysis`             | [3867–3882](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3867-L3882) | defense_producers          | Retry a failed defensive engine request.                                                                                                       |
| `refresh_game_defensive_threats`              | [3886–3901](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3886-L3901) | defense_producers          | Require analyzed game and enqueue its current-version threat scan.                                                                             |
| `list_defensive_threat_candidates`            | [3905–3916](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3905-L3916) | defense_producers          | Read current candidates with decoded evidence, validation, and analysis requests.                                                              |
| `dismiss_defensive_threat_candidate`          | [3920–3936](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3920-L3936) | defense_producers          | Dismiss a candidate with mapped missing/conflicting state errors.                                                                              |
| `approve_defensive_threat_candidate`          | [3940–3957](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3940-L3957) | defense_producers          | Approve a candidate, create/admit its card, and refresh the daily queue.                                                                       |
| `train_defensive_threat_candidate_now`        | [3961–3977](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3961-L3977) | defense_producers          | Explicitly queue an approved candidate for training.                                                                                           |
| `pause_defensive_threat_candidate`            | [3981–3997](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L3981-L3997) | defense_producers          | Pause candidate training eligibility.                                                                                                          |
| `resume_defensive_threat_candidate`           | [4001–4019](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4001-L4019) | defense_producers          | Resume candidate eligibility and schedule defense admission.                                                                                   |
| `get_defense_exercise`                        | [4023–4029](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4023-L4029) | defense_attempts           | Read an admitted defensive exercise through its public reader.                                                                                 |
| `attempt_defense_exercise`                    | [4033–4058](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4033-L4058) | defense_attempts           | Settle an answer against attempt/revision/queue identity and schedule analysis if required.                                                    |
| `recognize_defense_exercise`                  | [4062–4076](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4062-L4076) | defense_attempts           | Settle recognition attempt with stable idempotency identity.                                                                                   |
| `list_game_findings`                          | [4080–4097](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4080-L4097) | game_findings              | Validate paging/status filters and read decoded gameplay finding rows.                                                                         |
| `next_tactical_finding`                       | [4101–4111](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4101-L4111) | game_findings              | Read next due tactical finding and decode accepted-move/evidence payloads.                                                                     |
| `curate_tactical_finding`                     | [4115–4136](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4115-L4136) | game_findings              | Skip/defer or ignore a tactical miss after exclusion/type checks.                                                                              |
| `game_tactical_statistics`                    | [4140–4157](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4140-L4157) | game_findings              | Validate date/outcome/color filters and return tactical statistics.                                                                            |
| `decide_game_finding`                         | [4161–4192](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4161-L4192) | game_findings              | Accept/ignore a finding; require canonical linked miss event before prioritizing a repertoire lapse in Study.                                  |
| `create_card_from_game_finding`               | [4196–4219](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4196-L4219) | game_findings              | Preview or durably save a finding-derived card, then refresh Study queue.                                                                      |
| `exclude_game_from_adaptation`                | [4223–4247](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4223-L4247) | game_findings              | Toggle game exclusion, update pending findings, and refresh derivation/opportunities.                                                          |
| `game_motif_insights`                         | [4251–4252](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4251-L4252) | game_findings              | Return motif recommendations derived from gameplay.                                                                                            |
| `public_game_record`                          | [4255–4289](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4255-L4289) | game_queries               | Decode a saved game into its public transport model and derive adherence.                                                                      |
| `summary`                                     | [4293–4353](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4293-L4353) | game_queries               | Validate filters/cursor, page game summaries, look up matched positions, and encode next cursor.                                               |
| `game_position_summary`                       | [4357–4416](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4357-L4416) | game_queries               | Aggregate game outcomes and analysis loss/mistakes by move at a canonical position.                                                            |
| `game_detail`                                 | [4420–4425](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4420-L4425) | game_queries               | Read a game and serialize it through the public game model.                                                                                    |
| `chess_statistics_overview`                   | [4429–4432](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4429-L4432) | statistics                 | Validate reporting window and read game statistics overview.                                                                                   |
| `chess_statistics_breakdown`                  | [4436–4442](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4436-L4442) | statistics                 | Validate reporting window/dimension and read grouped statistics.                                                                               |
| `refresh_chess_statistics_day`                | [4446–4463](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4446-L4463) | statistics                 | Validate day and dispatch or schedule a daily statistics snapshot.                                                                             |
| `chess_statistics_insights`                   | [4467–4472](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4467-L4472) | statistics                 | Validate state and return decoded daily insights.                                                                                              |
| `start_guided_game_review`                    | [4476–4487](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4476-L4487) | guided_reviews             | Create or resume a guided review session through selected runtime.                                                                             |
| `guided_game_review`                          | [4491–4495](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4491-L4495) | guided_reviews             | Read/reconcile a saved guided-review session.                                                                                                  |
| `attempt_guided_game_review`                  | [4499–4521](https://github.com/aaweaver-actuary/tempo/blob/7d77ed1b25198dcd320e0265f777cc630f23775e/backend/app/main.py#L4499-L4521) | guided_reviews             | Settle a guided move and map persisted guided-review errors across runtime branches.                                                           |