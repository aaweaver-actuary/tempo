# Architecture drawing index

These maintained Mermaid views are reviewable maps of source, not deployment
certificates. Start at the system level, follow the component links, then use a
sequence to inspect the transaction or failure boundary in question. The
[adopted target](../component-reconstruction.md) and
[migration index #192](https://github.com/aaweaver-actuary/tempo/issues/192) remain
the target/migration authorities. Source wins if a drawing disagrees; correct the
drawing and disclose the discrepancy.

## Source and status

Baseline reviewed: `main` at
[`bc746728ca1967cc8ad9991cbe8c12099a01a3ae`](https://github.com/aaweaver-actuary/tempo/commit/bc746728ca1967cc8ad9991cbe8c12099a01a3ae),
verified October 20, 2026 after PR322 merged at 17:47:50 UTC. The initial review
used `4fc2eb8c`; the affected Games views were refreshed against the merge.
Relative source links describe the merged baseline and should
track later changes in the same PR as the affected code.

| Label | Meaning and limits |
| --- | --- |
| **MAIN** | Implemented source at the baseline; independent installation, consumer adoption and live deployment are separate claims. |
| **LEGACY / GAP** | Actual shared source, persistence or lifecycle coupling. A scoped observation, not acceptance of that coupling for new paths. |
| **MERGED PR322** | [Games-only viewing PR322](https://github.com/aaweaver-actuary/tempo/pull/322), implementation [`653a85bb5f5b3e520c0eda0c36234d3b416708b4`](https://github.com/aaweaver-actuary/tempo/commit/653a85bb5f5b3e520c0eda0c36234d3b416708b4), is now included in the MAIN baseline above. Source availability does not establish deployment or exact live read grants. |
| **PROPOSED** | Use this label and pinned PR source links for future unmerged changes; do not silently include them in MAIN. |
| **TARGET** | Adopted direction without an implementation claim. |
| **DEPLOYMENT UNVERIFIED** | No live process, configuration, resource allocation, credential, database or deployment was inspected or changed for these drawings. |

PR319's encountered-card practice proposal is paused and excluded from these
implemented views; it supplies no current API/behavior claim. The Games reader
views now describe merged PR322 source, while operational activation is unverified. The user-identified live
Study revision `86d11afe` is outside this inspection and was not touched.

## Views and maintenance owners

Owners below are component responsibilities, not invented individual assignments.
The implementing PR author updates the affected view; reviewers of the named
component and any changed contract check its correspondence. See the
[component registry](../../../components.json) for registration and qualification
ownership; its status alone does not prove adoption.

| View | Level / question | Maintenance owner and source entry |
| --- | --- | --- |
| [Extracted component map](system.md#implemented-component-increments) | System: what can run separately, and who owns each database? | Composition/affected service owners; [Learning](../../../services/learning/runtime/README.md), [Content](../../../services/content/README.md), [Game Evidence](../../../services/game-evidence/README.md), [Intelligence](../../../services/intelligence/README.md). |
| [Legacy assembly](system.md#legacy-assembly-and-existing-coupling) | System: where do current full-app dependencies cross those target boundaries? | Legacy app/backend owners; [Home](../../../app/views/home_view.tsx), [backend root](../../../backend/app/main.py), [Compose](../../../docker-compose.yml). |
| [Data, resource and lifecycle ownership](system.md#data-resource-and-lifecycle-owners) | Component: migrations, pools, shutdown and shared failure domains | Each state/resource owner; source links in the table. |
| [Study dependency detail](study-games.md#main-study-path-and-source-gap) | Component: which adapters and semantic ports serve Study, and where is Games still in shared source? | Study browser/session and Learning owners; [connection](../../../app/lib/study-connection.ts), [ReviewStore](../../../services/learning/application/src/tempo_learning_review/ports.py). |
| [Conditional Study build/offline assets](study-games.md#conditional-study-build-and-offline-assets-main) | Build/data flow: can emitted optional chunks enter the required offline shell without executing? | Study build and offline-shell owners; [inventory plugin](../../../scripts/offline-shell-assets.ts), [service worker](../../../public/sw.js), [readiness client](../../../app/lib/offline-shell.ts). Source checked; emitted artifact and live failure unverified. |
| [Shared Learning request execution gaps](study-games.md#shared-learning-request-execution-gaps-main) | Runtime/failure boundary: where can host publication and optional endgame work delay essential Study progress? | Learning HTTP, HostActivity and endgame adapter owners; [API](../../../services/learning/runtime/src/tempo_learning_runtime/api.py), [host publisher](../../../services/learning/runtime/src/tempo_learning_runtime/host_activity.py). MAIN HostActivity gap; PROPOSED bounded endgame owner with fake-ASGI/process-exit proof, native/deployment unverified. |
| [Games reader](study-games.md#merged-pr322-games-reader) | Component: UI capability, read projection, grants, lifecycle and resource budget | Workbench and Intelligence read owner; PR322 source links in the view. |
| [Review and lost-response recovery](flows.md#review-commit-and-recovery-main) | Sequence: durable browser intent, transaction owner, logical settlement, next load | Study session, Learning review and queue owners. |
| [Evidence and asynchronous admission](flows.md#observed-game-evidence-to-learning-main) | Sequence/data flow: independent producer, immutable delivery, receipts, lease fencing and queue effects | Game Evidence delivery, Learning receiver/remediation/admission owners. |
| [Publication and activation](flows.md#content-publication-and-serving-activation-main) | Data flow: immutable definitions versus serving authority | Content, bridge operator and Learning publication owners. |
| [Games read and failure](flows.md#games-read-failure-and-disposal-main-merged-pr322) | Sequence: accepted snapshot read, unavailable state, cleanup | Workbench Games reader and Intelligence transaction/lifespan owners; merged PR322 source, deployment unverified. |

## Drawing conventions

Flowchart arrows point from caller/consumer to capability/provider; labels name
the operation, transport or read/write effect. Dotted arrows explicitly label
navigation, serialized transfer or a source gap; they are not silently omitted
dependencies. Cylinders represent owned storage, subgraphs group composition or
ownership rather than guaranteeing a process/container. Sequence arrows show
calls and dashed arrows show results; notes name commit boundaries and durable
asynchrony. A receipt of acceptance does not imply queue admission or study
completion. Pure libraries may be shared without creating service dependencies.

Use GitHub-supported `flowchart` and `sequenceDiagram` fenced `mermaid` blocks,
quoted flowchart labels and stable node IDs. Avoid external assets, plugins,
click scripts, experimental diagram types or a single repository-sized graph.
Keep explanatory detail and source links beside each bounded view.

## Maintenance and review procedure

1. Identify changed interfaces, callers, databases, effects, transactions,
   commands/receipts, failure, cancellation, shutdown and resource owners. Search
   source and public exports as well as this index and `components.json`.
2. Update only affected system/component/sequence views in the same PR as the
   architecture change. Add a bounded view for an unrepresented changed boundary;
   its absence is not a no-impact reason. Split an unreadable graph by question. Link new nodes and
   important edges to concrete source files and symbols in nearby prose/tables;
   preserve named gaps instead of drawing only the desired design.
3. Check both ends of each edge. Verify who can read/write each state, which
   transaction commits receipts with effects, and what retries or loses authority
   after timeout, cancellation, stale generation or lost response. Study must have
   no Games call/import/wait/readiness/transaction/lifecycle dependency. Optional
   influence must enter Learning's semantic interface, never its tables directly.
4. Label source state and exact baseline. Keep unmerged diagrams pinned to the
   reviewed PR SHA. When adopted, verify the actual merge/source, move the view
   into MAIN, and replace proposal links; do not infer deployment from merging.
   Refresh only affected baseline/evidence statements and explain reuse limits.
5. Extract edited Mermaid blocks and parse with already available lightweight
   Mermaid tooling if present; report its version and limits. Check relative paths
   and anchors, pinned source links and symbol correspondence. Run
   `npm run check:conflicts` and `git diff --check` per [testing](../../testing.md).
   Inspect GitHub's rendered Markdown at useful width; parsing alone does not
   prove layout or GitHub's exact renderer version. Record any unavailable check.
   Do not install a new toolchain, start services or run heavy suites for drawings.
6. In the PR, write `Architecture impact:` with updated view links and the actual
   boundary change, or `Architecture impact: none` with a concrete reason. State
   relevant checks, exceptions, and deployment/resource claims still unverified.
   Reviewers check the graph against source, not just its appearance or test count.

This documentation criterion preserves the [MVP policy](../../../AGENTS.md#deliver-a-usable-single-user-mvp),
essential data/permission safeguards and existing required checks. It neither
requires every extraction/edge case before delivery nor excuses coupling in a
new or changed path that claims isolation.
