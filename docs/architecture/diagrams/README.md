# Architecture drawing index

These maintained Mermaid views are reviewable maps of source, not deployment
certificates. Start at the system level, follow the component links, then use a
sequence to inspect the transaction or failure boundary in question. The
[adopted target](../component-reconstruction.md) and
[migration index #192](https://github.com/aaweaver-actuary/tempo/issues/192) remain
the target/migration authorities. Source wins if a drawing disagrees; correct the
drawing and disclose the discrepancy.

Database table/key/field views live in the [database directory index](../../../database/README.md); physical colocation preserves the component owners shown here. The [unmerged frozen-order extraction](../../../backend/app/study/frozen_order/README.md) documents its bounded policy/storage wiring and remaining legacy caller coupling. The subsequent [authored Studies conflict split](../../../backend/app/studies/README.md) keeps line queries in the legacy database owner and pure conflict computation outside it.

The [unmerged backend organization view](../../../backend/app/README.md) maps the first separated policy/contract leaves and shared timing declaration. Its [finite checklist](../../../backend/ORGANIZATION.md) keeps remaining legacy workflows and technical composition gaps explicit; it does not change the MAIN/deployment status below.

## Source and status

Baseline reviewed: main at
[`588cea54574c2b56799f0a22f8235976171c4fba`](https://github.com/aaweaver-actuary/tempo/commit/588cea54574c2b56799f0a22f8235976171c4fba),
verified October 20, 2026 after the five scoped source repairs merged. PR328
moves the ten maintained diagram bodies into their owning directory READMEs,
reconciles them with that actual source, and adds no runtime behavior. Relative
source links describe this MAIN baseline. Historical feature heads below identify
reviewed inputs rather than unmerged behavior. The
[durable integration receipt](../study-isolation-integration-2026-10-20.md)
records exact code merge SHAs, source parity, selected evidence and cleanup.
No deployment or effective host resource isolation is implied.

| Label | Meaning and limits |
| --- | --- |
| **MAIN** | Implemented source at the baseline; independent installation, consumer adoption and live deployment are separate claims. |
| **LEGACY / GAP** | Actual shared source, persistence or lifecycle coupling. A scoped observation, not acceptance of that coupling for new paths. |
| **MERGED PR322** | [Games-only viewing PR322](https://github.com/aaweaver-actuary/tempo/pull/322), implementation [`653a85bb5f5b3e520c0eda0c36234d3b416708b4`](https://github.com/aaweaver-actuary/tempo/commit/653a85bb5f5b3e520c0eda0c36234d3b416708b4), is now included in the MAIN baseline above. Source availability does not establish deployment or exact live read grants. |
| **MERGED PR324** | [Physical Study composition PR324](https://github.com/aaweaver-actuary/tempo/pull/324) at [`30191b9c026f27f3d816304c47e5b72e0ec390b0`](https://github.com/aaweaver-actuary/tempo/commit/30191b9c026f27f3d816304c47e5b72e0ec390b0). Historical reviewed feature input, now included in the MAIN baseline; exact merge recorded in the integration receipt. Deployment unverified. |
| **MERGED PR325** | [HostActivity memory/publication isolation PR325](https://github.com/aaweaver-actuary/tempo/pull/325) at [`118389f1d933410ef377a47861001c4d6cf37060`](https://github.com/aaweaver-actuary/tempo/commit/118389f1d933410ef377a47861001c4d6cf37060). Historical reviewed feature input, now included in the MAIN baseline; exact merge recorded in the integration receipt. Deployment unverified. |
| **MERGED PR326** | [Optional endgame worker and pool ownership PR326](https://github.com/aaweaver-actuary/tempo/pull/326) at [`72d5d9c7ab964d5fbbb10528d67ce6cb28e77a18`](https://github.com/aaweaver-actuary/tempo/commit/72d5d9c7ab964d5fbbb10528d67ce6cb28e77a18). Historical reviewed feature input, now included in the MAIN baseline; exact merge recorded in the integration receipt. Deployment unverified. |
| **MERGED PR327** | [Borrowed review-policy transaction PR327](https://github.com/aaweaver-actuary/tempo/pull/327) at [`e07471d2e0eb7699e7992cb5f4a00892430cc0f1`](https://github.com/aaweaver-actuary/tempo/commit/e07471d2e0eb7699e7992cb5f4a00892430cc0f1). Historical reviewed feature input, now included in the MAIN baseline; exact merge recorded in the integration receipt. Deployment unverified. |
| **MERGED PR329** | [Learning queue persistence owner PR329](https://github.com/aaweaver-actuary/tempo/pull/329) at [`5f896cffdc29b228d36ea0b543c004100650b058`](https://github.com/aaweaver-actuary/tempo/commit/5f896cffdc29b228d36ea0b543c004100650b058). Historical reviewed feature input, now included in the MAIN baseline; exact merge recorded in the integration receipt. Deployment unverified. |
| **PR328 DOCS PLACEMENT** | Canonical directory-README diagram placement at [`68f1ab115b6c96846ba5e56373f7348361edd32e`](https://github.com/aaweaver-actuary/tempo/commit/68f1ab115b6c96846ba5e56373f7348361edd32e), reconciled here with the five merged source inputs. |
| **PROPOSED** | Use this label and pinned PR source links for future unmerged changes; do not silently include them in MAIN. |
| **TARGET** | Adopted direction without an implementation claim. |
| **DEPLOYMENT UNVERIFIED** | No live process, configuration, resource allocation, credential, database or deployment was inspected or changed for these drawings. |

PR319's encountered-card practice proposal is paused and excluded from these
implemented views; it supplies no current API/behavior claim. The Games reader
views now describe merged PR322 source, while operational activation is unverified. The user-identified live
Study revision `86d11afe` is outside this inspection and was not touched.


## Implemented component increments

The [proposed local pgAdmin companion](../../../config/pgadmin/README.md#ownership-and-startup)
has its own bounded operational view. It is not activated or a Study dependency.

**MAIN:** these implementations coexist with the
[legacy assembly](../../../backend/README.md#legacy-assembly-and-existing-coupling). This
view shows source interfaces available for explicit composition, not a claim that
all users or live data have moved to them. `apps/study-web` remains a planned
independent artifact; the Learning-connected Study build reuses `static`/`app`.
`R/W` storage edges mean reads/writes by that state's owning component.

```mermaid
flowchart TB
  Study["Study browser<br/>static / app"] -->|"queue / review HTTP"| Learning["Learning runtime<br/>Queue / settlement owner"]
  Learning -->|"R/W"| LDB[("Learning PostgreSQL")]
  Bridge["A bridge operator<br/>Public CLI/HTTP adapters"] -->|"prepare / approve / export"| Content["Content<br/>Sources / publications"]
  Content -->|"R/W"| CDB[("Content PostgreSQL")]
  Bridge -->|"stage / activate / retire"| Learning
  Content -.->|"record CLI: authenticated<br/>original receipt GET"| Learning
  GE["Game Evidence<br/>Sources / comparisons / outbox"] -->|"R/W"| GDB[("Game Evidence PostgreSQL")]
  Bridge -.->|"explicit snapshot load"| GE
  GE -->|"EvidenceDelivery HTTP"| Learning
  WB["Workbench<br/>Analysis / selected games"] -->|"reads / explicit commands"| Intel["Intelligence<br/>Inputs / reports / jobs"]
  Intel -->|"R/W"| IDB[("Intelligence PostgreSQL")]
```

The bridge is an explicit operator tool, not an automatically invoked Learning
dependency. Content's explicit record CLI also makes its own authenticated
Learning receipt read before recording a result; see the
[publication sequence](../../../services/content/README.md#content-publication-and-serving-activation-main).
The former external A-export pilot is retired from maintained source; its
[last source snapshot](https://github.com/aaweaver-actuary/tempo/tree/b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8/tools/analysis-restoration/c_pilot)
is historical reference, not an active edge in this view. Intelligence also
implements Explorer source work and Maia job/result APIs, omitted here to bound the graph: its
[provider worker](../../../services/intelligence/src/tempo_intelligence/worker.py)
owns HTTP source calls, [Maia validation](../../../services/intelligence/src/tempo_intelligence/maia.py)
binds registered jobs/results, and the [Maia inference core](../../../workers/maia/README.md#retained-inference-boundary)
owns model computation. Its unused standalone supervisor/CLI is retired; the root
coverage scripts and Dockerfile.maia retain their separate execution path. These APIs do not grant Learning admission authority or
establish a deployed worker connection.

| Edge / boundary | Concrete source and interface |
| --- | --- |
| Study build and connection | [Vite target](../../../vite.static.config.ts), [static entry](../../../static/src/main.tsx), [Study connection](../../../app/lib/study-connection.ts) (`learningFetch`, origin/path allowlist). [Study artifact status](../../../apps/study-web/README.md) is still planned. |
| Learning HTTP, transaction and install closure | [`create_app`](../../../services/learning/runtime/src/tempo_learning_runtime/api.py), [`Database`](../../../database/tempo_learning_database/connections.py), [installed dependencies](../../../services/learning/runtime/pyproject.toml). Game Evidence **contracts**, not its service implementation, are installed. |
| Content/bridge and activation | [Content `Store`](../../../database/tempo_content_database/adapters/store.py), [bridge domain](../../../services/content/src/tempo_content/bridge/__init__.py), [operator](../../../tools/analysis-restoration/a_bridge/src/tempo_a_bridge/cli.py), [Learning publication bridge](../../../services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py). See [activation flow](../../../services/content/README.md#content-publication-and-serving-activation-main). |
| Game Evidence output and delivery | [`Store`](../../../database/tempo_game_evidence_database/adapters/store.py), [`deliver_one`](../../../services/game-evidence/src/tempo_game_evidence/delivery.py), [analysis export](../../../services/game-evidence/src/tempo_game_evidence/analysis_export.py), [Learning receiver](../../../services/learning/runtime/src/tempo_learning_runtime/receivers.py). |
| Workbench / Intelligence | [`IntelligenceClient`](../../../apps/workbench-web/src/client.ts), [`PendingCommands`](../../../apps/workbench-web/src/commands.ts), [HTTP composition](../../../services/intelligence/src/tempo_intelligence/api.py), [Analysis routes](../../../services/intelligence/src/tempo_intelligence/analysis/api.py), [`AnalysisStore`](../../../database/tempo_intelligence_database/adapters/analysis_store.py). |
| Engine dispatch | [bounded Intelligence dispatcher](../../../services/intelligence/src/tempo_intelligence/analysis/worker.py), injected client interface (no concrete worker dependency), [job/result contracts](../../../contracts/analysis-jobs/README.md). The standalone Stockfish worker/client is retired; see its [pre-removal snapshot](https://github.com/aaweaver-actuary/tempo/tree/cb0ed5910e6bd05894919fd8d49f5dc7bc236975/workers/stockfish). Maia and native/browser engine paths remain. The external A-export pilot is retired; see its [historical source snapshot](https://github.com/aaweaver-actuary/tempo/tree/b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8/tools/analysis-restoration/c_pilot). |
| Shared libraries | [scheduling](../../../packages/scheduling/README.md), [chess identity](../../../packages/chess-core/README.md), [browser intents](../../../database/browser-intents/README.md), [Study session](../../../packages/study-session/README.md), [board renderer](../../../packages/board-ui/README.md), [contract index](../../../contracts/README.md). Package existence is not proof of every consumer's adoption. |

## Views and maintenance owners

Each Mermaid block has one canonical home in a directory `README.md`. The system
overview renders above; the component and sequence blocks render in the owning
README sections linked below. This makes directory navigation useful without
duplicating drawing bodies. The former [system](system.md),
[Study/Games](study-games.md) and [sequence](flows.md) pages retain their headings
as compatibility pointers and cross-cutting notes, not second copies.

Owners below are component responsibilities, not invented individual assignments.
The implementing PR author updates the affected view; reviewers of the named
component and any changed contract check its correspondence. See the
[component registry](../../../components.json) for registration and qualification
ownership; its status alone does not prove adoption.

| View / canonical README section | Level / question | Maintenance owner and source entry |
| --- | --- | --- |
| [Extracted component map](#implemented-component-increments) | System: what can run separately, and who owns each database? | Composition/affected service owners; [Learning](../../../services/learning/runtime/README.md), [Content](../../../services/content/README.md), [Game Evidence](../../../services/game-evidence/README.md), [Intelligence](../../../services/intelligence/README.md). |
| [Legacy assembly](../../../backend/README.md#legacy-assembly-and-existing-coupling) | System: where do current full-app dependencies cross those target boundaries? | Legacy app/backend owners; [Home](../../../app/views/home_view.tsx), [backend root](../../../backend/app/main.py), [Compose](../../../docker-compose.yml). |
| [Data, resource and lifecycle ownership](system.md#data-resource-and-lifecycle-owners) | Component: migrations, pools, shutdown and shared failure domains | Each state/resource owner; source links in the table. |
| [Study dependency detail](../../../app/README.md#main-study-path-and-source-gap) | Component: which adapters and semantic ports serve Study, and how is optional workspace ownership excluded from Study? | Study browser/session and Learning owners (merged MAIN source); [connection](../../../app/lib/study-connection.ts), [ReviewStore](../../../services/learning/application/src/tempo_learning_review/ports.py). |
| [Conditional Study build/offline assets](../../../static/README.md#conditional-study-build-and-offline-assets-main) | Build/data flow: can emitted optional chunks enter the required offline shell without executing? | Study build and offline-shell owners; [inventory plugin](../../../scripts/offline-shell-assets.ts), [service worker](../../../public/sw.js), [readiness client](../../../app/lib/offline-shell.ts). PR324 retains actual emitted-artifact and synthetic offline evidence; combined deployment unverified. |
| [Shared Learning request execution gaps](../../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main) | Runtime/failure boundary: where can host publication and optional endgame work delay essential Study progress? | Learning HTTP, HostActivity and endgame adapter owners; [API](../../../services/learning/runtime/src/tempo_learning_runtime/api.py), [host publisher](../../../services/learning/runtime/src/tempo_learning_runtime/host_activity.py). Host ownership remains; the proposed endgame calculation removal retires its worker/pools and preserves saved receipt/practice boundaries. The receipt remains historical evidence for its dated assembly, not removal qualification. |
| [Games reader](../../../apps/workbench-web/README.md#merged-pr322-games-reader) | Component: UI capability, read projection, grants, lifecycle and resource budget | Workbench and Intelligence read owner; PR322 source links in the view. |
| [Review and lost-response recovery](../../../services/learning/runtime/README.md#review-commit-and-recovery-main) | Sequence: durable browser intent, transaction owner, logical settlement, next load | Study session, Learning review and queue owners. |
| [Evidence and asynchronous admission](../../../services/game-evidence/README.md#observed-game-evidence-to-learning-main) | Sequence/data flow: independent producer, immutable delivery, receipts, lease fencing and queue effects | Game Evidence delivery, Learning receiver/remediation/admission owners. |
| [Publication and activation](../../../services/content/README.md#content-publication-and-serving-activation-main) | Data flow: immutable definitions versus serving authority | Content, bridge operator and Learning publication owners. |
| [Games read and failure](../../../apps/workbench-web/README.md#games-read-failure-and-disposal-main-merged-pr322) | Sequence: accepted snapshot read, unavailable state, cleanup | Workbench Games reader and Intelligence transaction/lifespan owners; merged PR322 source, deployment unverified. |

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
   its absence is not a no-impact reason. Place the canonical block directly in
   the relevant owning directory's `README.md`; keep this README's overview and
   placement links current. For a cross-component drawing, choose one README home
   and link from peer directories. Move existing blocks/source notes and update
   references instead of copying a drawing. Split an unreadable graph by question. Link new nodes and
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
