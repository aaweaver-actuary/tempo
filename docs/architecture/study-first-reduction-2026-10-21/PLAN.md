# Execution plan: converge Study, retire everything else by default

**Status:** Planning and discovery; no deployment, mutation, deletion, or migration authorized by this document. Read [decision](./DECISION.md), [feature register](./FEATURES.md), [audit](./AUDIT.md), [Study boundary](./STUDY-BOUNDARY.md), and [database audit](./DATABASE-AUDIT.md) as required for a specific task. Do not load the full 881-module index on every agent run.

## 0. Publish the audit and pin the source [documentation task]

- Keep the exact `7d77ed1b25198dcd320e0265f777cc630f23775e` snapshot intact. Full historical module/table indexes are point-in-time navigation aids, not proof of usage.
- Treat `main.py` as fully read, others as their individual recorded review depths; do not claim 881 complete semantic reviews.
- Establish a new keep/retire matrix per product capability rather than rerunning the old 12-stage roadmap.
- Outcome: decision, registry, source-pinned audit and executable child issues in GitHub.

## 1. Fingerprint current Study (read-only)

- From the **actual** phone and desktop origins/launchers, record URL, frontend build, API base, running image/git SHA and entry point. Inspect the actual service composition, health and API routes, not just `main`.
- Identify active PostgreSQL installations/migration versions and user data owners; collect read-only schema/row/size/dependency metadata; exclude credentials/secrets from public reports.
- Observe one normal queue issue, one opening introduction per active repertoire, one review, replay/reload and phone reconnection. Where observing would mutate live learning state, use existing real activity/logs or an isolated faithful clone; **do not manufacture live reviews for an audit**.
- Confirm differences between legacy prepared-batch offline and independent one-card offline behavior, and explicitly mark accepted losses.
- **Exit:** exact serving graph, protected source/state/route list, minimal supported behavior matrix. No cutover assumption remains unstated.

## 2. Freeze the MVP contract; verify independent Learning

**Target proposed:** **one responsive Study Web artifact + one Learning API + one Learning PostgreSQL**, with chess core/scheduling and accepted content artifacts. No Redis, Celery, legacy `main`, Games, Workbench, providers or engines needed for ordinary Study.
- Same web client and endpoint on phones and desktops; no separate device policy or source tree.
- Due reviews/new openings from every enabled repertoire; stable sequence and quotas.
- Atomic answer history, schedule, queue, receipt and progression; real replay after lost response, reload after commit/before next card, stale card/revision, concurrent-device reentry.
- Allowed simplification: small explicit offline cache (one pending card) or honest connected-only mode if separately accepted. Do not leave a phone-only offline engine with different review logic.
- Prove build/import boundaries and test with optional services physically absent, not just flags disabled.
- **Exit:** independent Study executable proves the agreed matrix against disposable representative data.

## 3. Preserve one simple repertoire import path

- Use existing Content parsing/publication/activation contracts where they are simpler than writing a new pipeline. Support at least the source format the user actually imports (PGN and deliberate active repertoire selection).
- Validate legal moves, source revisions/identity, duplicate/transposition rules that affect issued cards, and owned publication/activation. No need to keep a full Builder/Workbench UX; one minimal upload screen or documented simple interface is sufficient.
- Reimport should not reset established review history or resurrect deleted revisions. No Content runtime dependency on daily Study after accepted publication.
- **Exit:** new and updated repertoire reaches Study; old accepted content survives Content stopped.

## 4. Rehearse exact minimal-state transfer and cross-device cutover

- Export latest real owned data/read receipts under appropriate authorization; preserve source/card identities, FSRS/history/due dates, introduced/unintroduced facts, queue state, accepted generations and pending operations.
- Use isolated destination/database and reproducible mapping, with explicit unhandled fields. Semantic before/after checks plus phone/desktop smoke tests; design rollback before any live write.
- Deliberately select the new shared URL/entry point, then seek separate cutover approval. **No automatic shutdown, data migration or redeployment in this planning issue.**
- **Exit:** approved rollbackable cutover with verified current real data and agreed offline concessions; two devices genuinely hit one implementation.

## 5. Delete obsolete code in complete dependency-closed slices

Only after phase 4's evidence (or for independently proven dormant slices while Study stays protected):
1. Remove legacy app routes/coordinator/legacy worker registrations and branches no longer reached by the selected Study/Content deployment. Document exceptions in the dependency boundary.
2. Remove retired Games ingestion, analysis, threat/defense, statistics, guided-review, discovery/coverage, model inference, optional frontend and related commands, triggers/caches and runtime wiring **as entire capabilities**.
3. Prune dead adapters, unused compatibility exports, default Compose services, image build contexts, packages, scripts, and exclusive tests/docs. Keep tests for behavior that survives.
4. Re-run narrow Study/Content acceptance with deleted source physically absent. The supported runtime must not have dormant imports of retired packages.
5. Count net code removed (same 597-file / 69,686-line narrow denominator, plus broader 881 / 97,217 scope). File moves and formatting are not reduction.
- **Exit:** smaller production source tree with independently demonstrated retained Study/Content behavior, not an `archive/` copy.

## 6. Reduce persistence only after code retirement

- Compare the 292 owner-qualified source table entries with actual installed schemas, reset-age-qualified usage counters, views/triggers/FKs, and supported imports/exports.
- For one table family at a time, establish authoritative owner, all direct/indirect consumers, retained user history, migration/restore method and code owner removed. Zero scans and `_legacy` names alone are insufficient evidence.
- Drop tables only in explicit data migrations with backup/rollback and separate approval. Database/table reductions are an additional outcome, **not** prerequisite to deleting unused code.
- **Exit:** lean owned schema, consistent migrations, no stale runtime references.

## 7. Guard against regrowth

- One short README per surviving domain listing entry point, public operations, owned state, build/test command, and forbidden imports.
- Avoid generic repository/transaction/orchestration frameworks. Prefer deleting features to multiplying wrappers.
- Codex task kickoff specifies exact owned files and one narrow test command; do not send giant inventories to agents as default context.
- Close/supersede retired optional issues only after the linked capability is truly removed; historical issue text stays searchable.
- Measure time-to-understand for representative changes and the completed before/after line totals.

## Minimum acceptance matrix

| Check | Must hold |
|---|---|
| Phone and desktop same implementation | Same Study frontend artifact and Learning API; no device branch delegating to legacy |
| Ordinary daily Study | Due reviews and new openings from each active repertoire |
| Durable progress | One correct/wrong review has one logical effect, after retries |
| Device and network recovery | Reload, lost response, reconnect, simultaneous devices do not duplicate/silently lose answers |
| Content import | PGN arrives, new cards become eligible; reimport preserves unaffected history |
| Optional services stopped | Study continues with Games, analysis, inference, browser Workbench and legacy workers absent |
| Removal completeness | Surviving images/install paths/CI no longer import/build retired features |
| Data safety | No live table drops until separately approved with restore route |

## Dependency/parallelism

One owner traces actual deployment + database; one owner qualifies independent Study UI/API; one owner scopes minimal Content import. Work can proceed in parallel in isolated branches after sharing the agreed contract; only one owner modifies the final live cutover and data mapping. Do not initiate broad optional feature work during this reduction. Historical source: [#261](https://github.com/aaweaver-actuary/tempo/issues/261), [#266](https://github.com/aaweaver-actuary/tempo/issues/266), [#267](https://github.com/aaweaver-actuary/tempo/issues/267), [#268](https://github.com/aaweaver-actuary/tempo/issues/268); their prior optional assembly scope is not the new product target.