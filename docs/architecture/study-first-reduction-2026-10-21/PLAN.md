# Execution plan: small, explicit Study-first tasks

Revised 2026-10-22. Programme: [#357](https://github.com/aaweaver-actuary/tempo/issues/357). This replaces the earlier broad phase assignments, not the preserved source audit. Issue bodies hold the exact writable files, commands and acceptance; this document is their index, not a second task specification.

**Assign one ready leaf, never a parent programme.** Read [SMALL-TASKS.md](./SMALL-TASKS.md) for the execution contract. This plan authorizes isolated task work only; it does not authorize merging, deploying, modifying live data, deleting tables or changing hosted CI settings.

## 1. Reuse what is already built

The reviewed main checkpoint is `0e59bb81e5f0baa06504230fab072bc8424ba1e0`, which merged #370. Its tree matches repaired frontend commit `ebe724c34a41533027cfc5b4040d4a3e0680c586`. #364 is complete for its declared frontend build/source-removal scope; do not repeat that extraction or infer that all public assets and the deployed application are qualified.

At this planning checkpoint, #371 has published documentation repairs at `4584bc4f` and awaits review. #372's description reports `e6704daf`, while the PR metadata and branch ref returned `a3ab4a10`. #365 therefore starts with published-source reconciliation, not another implementation. Recheck live PR metadata before assignment; these are dated observations, not permanent branch facts. No plan task owns or merges the active PRs on another agent's behalf.

## 2. Fixed product and interface decisions

- One responsive `TEMPO_TARGET=study` frontend and one Learning authority serve both device types. The existing split is legacy versus independent build, not necessarily phone versus desktop.
- The opt-in disposable composition has three long-lived services: `study-web`, `learning`, `postgres`. One browser origin proxies API and health requests to `learning:8001`. Do not expose the database or inject a service credential through the web proxy.
- Learning retains existing review, issuance, quota, scheduling, transaction and retry ownership. Its restricted reader/writer credentials and explicit owner migrations remain. No broker or optional analysis producer is a prerequisite.
- The shared offline contract retains the currently issued card and its durable immutable answer for original-authority replay. No separate phone scheduler or full local offline queue is added.
- Fresh authored PGN uses explicit workspace/source/color identity and the existing Content manifest/publication contract. Reuse the existing approval and operator delivery path, not a new transport/workflow framework. Content can stop after acceptance.
- Existing history is not fresh import. Real card/decision/policy identity, reviews, FSRS, introductions, order, attempts and browser/server pending work require a separately reviewed migration map. Move text or equal FEN is not proof of historical identity.
- Retire unsupported implementations before reorganizing their surviving neighbors. Keep saved facts and execution of retained exercises independently of their retired producers.

## 3. Task queue and dependency graph

All tasks below are linked issues with parent checklists. They are not asserted to be GitHub native sub-issue relationships. `Small` means the decisions and interfaces are supplied; it is not a guarantee that every model will implement them correctly. Infrastructure still needs a capable reviewer. `Expert` means do not delegate unresolved authority/data decisions to a cheap implementer.

### Cheap qualification and metrics — parent #363

| Task | One result | Depends on | Suggested owner |
| --- | --- | --- | --- |
| [#373 T01](https://github.com/aaweaver-actuary/tempo/issues/373) | Pure two-profile fixture plan | Ready | Small |
| [#374 T02](https://github.com/aaweaver-actuary/tempo/issues/374) | Essential-only inputs in existing native runner | #373 | Medium |
| [#392 T20](https://github.com/aaweaver-actuary/tempo/issues/392) | Read-only classified Git line delta | Ready; reviewer supplies real classification before baseline use | Small |

Research found unconditional Game Evidence producer, Content and a_bridge setup in `database/tempo_learning_database/tooling/run_postgres.py`, even for selected core tests. The two fixed profiles remove that overhead for essential cases while preserving the old full default. They do not add a generic dependency solver or change Learning's installed shared contract packages.

### Client invariants — parent #366

| Task | One result | Depends on | Suggested owner |
| --- | --- | --- | --- |
| [#375 T03](https://github.com/aaweaver-actuary/tempo/issues/375) | One-issued-card policy/storage tests | Ready | Small |
| [#376 T04](https://github.com/aaweaver-actuary/tempo/issues/376) | Original-authority recovery tests | Ready | Small |

These own different test files. First locate existing equivalent assertions: a no-code qualification can complete a verification task. A failure becomes a minimal separately reviewed repair; it is not permission to redesign the browser protocol.

### Opt-in composition — parent #367

| Task | One result | Depends on | Suggested owner |
| --- | --- | --- | --- |
| [#377 T05](https://github.com/aaweaver-actuary/tempo/issues/377) | New Study web image and same-origin proxy | Merged #370 | Medium |
| [#378 T06](https://github.com/aaweaver-actuary/tempo/issues/378) | Explicit disposable database bootstrap | Existing owner APIs | Medium + reviewer |
| [#379 T07](https://github.com/aaweaver-actuary/tempo/issues/379) | Three-service Compose configuration | #377, #378, accepted #365 | Small |
| [#380 T08](https://github.com/aaweaver-actuary/tempo/issues/380) | One actual wiring/startup failure smoke | #379 | Medium |

Use new opt-in files. The root Compose, default web image/proxy and registered `tempo` installation remain unchanged. The bootstrap reuses existing provision/migrate/grant APIs and refuses non-disposable/existing targets; it is not a production installer. No borrowed live volumes, secrets or implicit schema setup.

### Retained integrated behavior — parent #368

| Task | One result | Depends on | Suggested owner |
| --- | --- | --- | --- |
| [#381 T09](https://github.com/aaweaver-actuary/tempo/issues/381) | Ten explicit existing essential scenario selections | #374 | Small |
| [#382 T10](https://github.com/aaweaver-actuary/tempo/issues/382) | One pinned native and composed-browser receipt | #365, #375, #376, #380, #381 | Medium |

The ten cases are normal, teaching, lost-response, before-commit, next-load, two-tabs, offline-shell, wrong-credential, origin-transition and quota. Preserve their assertions. Actual installed image, native harness and real Compose/browser evidence remain distinct; one is not relabeled as another. #382 owns the expensive combined run and returns minimal failures to their owners.

### Minimal repertoire intake — parent #360

| Task | One result | Depends on | Suggested owner |
| --- | --- | --- | --- |
| [#383 T11](https://github.com/aaweaver-actuary/tempo/issues/383) | Pure fresh-PGN manifest generation | Ready | Small |
| [#384 T12](https://github.com/aaweaver-actuary/tempo/issues/384) | No-database manifest/preview CLI | #383 | Small |
| [#385 T13](https://github.com/aaweaver-actuary/tempo/issues/385) | Exact existing approval/delivery commands | #384 | Small |
| [#231](https://github.com/aaweaver-actuary/tempo/issues/231) | Existing unranked fallback/frozen-order contract proof | Ready | Small |
| [#386 T14](https://github.com/aaweaver-actuary/tempo/issues/386) | Native import/retry/history-preservation proof | #384, #385, accepted #365; #231 contract | Medium |

`bridge/application.py::map_import` already accepts validated PGN plus explicit assignments. #383 freezes the deterministic key formulas and limits in its issue. `tempo_a_bridge.cli.Operator.apply` already checkpoints and reconciles the original operation and Learning authority. Reuse that on-demand path; do not invoke its separate Games snapshot workflow. Its useful apply/execution-bundle code is a retained dependency even though the directory name contains `analysis-restoration`.

### Data and source retirement gates — parents #267, #361, #362

| Task | One result | Depends on | Suggested owner |
| --- | --- | --- | --- |
| [#387 T15](https://github.com/aaweaver-actuary/tempo/issues/387) | Metadata-only census query, not a live census | Ready | Small |
| [#388 T16](https://github.com/aaweaver-actuary/tempo/issues/388) | Pure exact normalized-snapshot comparison | Ready | Small |
| [#389 T17](https://github.com/aaweaver-actuary/tempo/issues/389) | Real-version field/identity map and golden fixtures | #358 actual observation | Expert + host |
| [#390 T18](https://github.com/aaweaver-actuary/tempo/issues/390) | Exact reviewed deletion cohort manifest | Retained #382/#386 proof and release gates | Expert |
| [#391 T19](https://github.com/aaweaver-actuary/tempo/issues/391) | Mechanical execution of approved `legacy-01` | #390 and applicable cutover gates | Small |

These are intentionally asymmetric: preparation may be cheap; authority decisions are not delegated without evidence. A snapshot comparator cannot detect facts an unqualified exporter omitted. A zero SQL-reference or scan count cannot certify a table drop. An exact reviewed file list lets a cheaper implementer remove code without guessing dependencies.

## 4. Work that must not be invented now

[#358](https://github.com/aaweaver-actuary/tempo/issues/358) is blocked on actual host/device/database access. It does not block pure tests, local tooling or isolated infrastructure work. Do not repeatedly ask for unavailable diagnostics. Never use queue GETs as a passive live census: they can issue work.

After #389 freezes the historical map, #267 releases four separate converter leaves: content/identity; reviews/schedules/introductions; server queue/attempt/receipt facts; browser pending envelopes. Each requires exact input/output versions, allowed files and golden tests before implementation. Do not ask an agent to discover aliases while writing a converter.

After the launcher specification (#369/#371), actual target fingerprint and data rehearsal are accepted, #359 releases separate leaves for the pure v1-to-v2 registration/receipt plan, the existing-lock lifecycle transition on disposable targets, and the authorized operator cutover. Normal diagnostics stay read-only; backup does not migrate. After target activation or uncertain acceptance, retain the authoritative Learning database and repair forward rather than silently returning to legacy or restoring a stale snapshot.

#362 releases a table-family migration only after actual metadata, consumers and retained-data disposition are established. Source removal and physical data removal are separate approvals. Retiring an unused installation and retaining history cold may be simpler than consolidating every table.

## 5. Parallel ownership and economical verification

Start #373, #375, #376 and #383 independently; #231/#387/#388/#392 are additional pure tasks. #377 and #378 may run with infrastructure-capable owners. Do not assign every task at once: each owner needs the named dependencies and an unclaimed writable set.

Serial shared-file ownership: #372 owns current runtime readiness; #374 owns the native runner; #381 owns the integration runner; #384 then #385 own the Content CLI/README sequence. #379 waits for image/bootstrap contracts. Qualification owners do not patch another owner's production code during a failing run.

Run leaf-local tests while iterating. Run native locking/replay/security checks at their actual owning boundary, and combined checks once for matching assembled executable inputs. Preserve a compact exact receipt; do not create a second evidence platform. Report no-code reuse, failures and unavailable infrastructure honestly. A code build, a merged PR, native fixture success and real deployment are different claims.

## 6. Historical backlog disposition

On 2026-10-22, all 52 initially open issues were reviewed. Fourteen retained issues were rewritten as current parents, gates or leaves; twenty new bounded tasks were created. Thirty-eight older issues were closed `not_planned` as superseded/deferred, not as implemented. Their original descriptions, comments, source links and PR evidence remain available. Closing an implementation request does not delete its code or waive a retained invariant.

| Historical scope | Issues | Current home |
| --- | --- | --- |
| Optional analysis, inference, generation, scoring and refinements | #4, #40, #43, #156, #190, #218, #258, #259, #292, #303, #304, #305, #321 | #357 feature disposition; reuse only explicitly selected code |
| Study/session/UI and optional-pause reconstruction | #13, #194, #198, #219, #230, #232, #249, #252, #254, #255, #256 | #359, #365, #366, #368; selected intake proof #386 |
| Content approval/import reconstruction | #193, #257, #265 | #360 and historical identity #267 |
| Broad toolchain/build/fixture/CI roadmap | #201, #202, #205, #251, #253, #263 | #363 and storage disposition #362 |
| Installer and whole-system assembly | #74, #260, #261, #266 | #359, #369, #368, #267 |
| Duplicate retirement tracker | #268 | #361 |

The older #192/M01-M12 roadmap is historical guidance, not an instruction to finish optional services. Existing open PRs are not automatically merged or closed by this plan; their owners must reconcile them with selected scope. No branch or source deletion was performed by backlog cleanup.

## 7. Measure actual reduction

Keep the frozen audit baseline `7d77ed1b`: 597 narrow backend/database Python+SQL files, 69,686 physical lines, target approximately 34,843 net removed. The broader 881-file implementation/schema scope is 97,217 lines. Use an approved classification manifest; do not relabel directories to appear smaller. Moves count at both ends, and test/docs/generated inventories have separate totals. Formatting alone is not functional retirement.

The [2026-10-25 reviewer-checked accounting receipt](./source-accounting-2026-10-25/README.md) verifies both original denominators and compares them with main `782bf537`: narrow 69,309 lines (net −377), broad 94,646 (net −2,571, including new runtime source). It preserves the historical broad helper-classification inconsistency, separates nonproduction/moves, and leaves 34,466 further comparable narrow physical lines against the original target. These are physical source measures, not certified functional retirement or 50% completion.

The 292 table entries are source-schema observations across owners, not an installed-table count. Audit review-depth labels remain intact. Update running decisions and feature disposition; never rewrite historical evidence to claim a larger review or completed deployment.
