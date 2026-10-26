# Tempo product specification

Version 0.2 · October 26, 2026 · Single-user, local-first product

Next-release encounter amendment · October 26, 2026 · Approved targets, not installation claims.

## 1. Purpose and authority

Tempo helps Andy recall his repertoire, learn from his games, prepare for likely opponent replies and common mistakes, and develop longer connected sequences without losing focused practice on difficult moves.

**Keep working Study and Tactics in place. Restart unfinished workflows selectively when that is faster than salvaging their orchestration. Deliver useful behavior before completing a replacement architecture.**

This is the product baseline, consolidated from the owner-provided v0.1 specification and subsequent clarifications, including the **30 CP** correction and adaptive card recombination. It describes required behavior, not a claim that every feature already works. Explicit implementation defaults and remaining policy choices are identified below.

For product scope and delivery order, this document takes precedence over conflicting older roadmaps, cleanup plans, and adaptive-segmentation proposals. Those remain historical or implementation references. Existing data-safety, permission, transaction, and actual required-check rules remain in force. Publishing this specification does not authorize deployment, data deletion, a learning-history reset, or a cutover.

The first useful feature must not depend on completing an independent Learning replacement, full history migration, the code-reduction target, a services framework, or a general-purpose analysis system. Preserve current content and learning history in place; do not migrate them merely to add optional workflows. Small owning-component changes are allowed when needed for a feature.

## 2. Required outcomes

| ID | Outcome |
| --- | --- |
| R01 | Preserve responsive Study, existing learning history, and reliable review saving. |
| R02 | Import Lichess and Chess.com games; bring genuine misses on existing introduced cards back to Study within 1–2 days, with at least the learning impact of a Study miss. Actual encounters with approved repertoire decisions still unintroduced receive priority first introduction. |
| R03 | Report uncovered opponent responses separately from recall failures. |
| R04 | Find likely missing repertoire moves using rating-appropriate Explorer and Maia evidence. |
| R05 | Prefer exact repertoire continuations and familiar responses from similar positions when repairing gaps. |
| R06 | Allow at most one own response per exact position within each repertoire; block conflicting material until manually resolved. |
| R07 | Find inconsistent own responses among similar positions and propose an acceptable, more familiar response. |
| R08 | Preserve current tactics: capture, validation, collection/pack controls, solving, and Study integration. |
| R09 | Introduce approved, unstudied opening cards according to encounter likelihood, updating priorities as personal-game evidence changes. |
| R10 | Prepare for common opponent blunders, including effective responses already in the repertoire but not yet learned. |
| R11 | Progressively rejoin learned fragments into longer training sequences after three consecutive clean reviews, while retaining repeated-failure splitting. |

## 3. Game feedback

### G01 — Import and classify before changing learning state

Separate provider adapters fetch games into one typed PGN representation. Preserve provider/game identity, player color, game time and its precision, move history, and import provenance. Import incrementally with retry-safe checkpoints. A bounded provider response must not silently truncate a requested date range.

A pure comparer reads an immutable repertoire snapshot and normalized game. It distinguishes:

- A confirmed own-move miss on a card introduced before the game.
- An uncovered opponent reply or a missing own continuation.
- A covered decision whose card is unintroduced or unavailable.
- Ambiguous identity, an exact-response conflict, or the intentional end of repertoire coverage.

Only a confirmed recall miss produces a lapse effect. Missing content, ambiguity, and unintroduced material must not be mislabeled as forgetting. Keep the original game position, expected response, played response, and exact mapped decision/card visible.

Do not generate a cascade of downstream misses after leaving coverage. Comparison may resume at an exact re-entered/transposed position only when repertoire and card mapping are unambiguous. A later reentry does not erase or relabel an earlier gap.

### G02 — One learning owner; a real learning effect

Study/Learning owns schedules, review effects, priority, queue admission, and settlement. Game feedback submits a typed command through that owner; it never writes learning tables or becomes a second scheduler.

A qualifying game miss must have **at least as strong an impact as a Study Again**, not just a cosmetic priority label. The implementation default is the same lapse scheduling transition plus a durable high-priority obligation. Record game provenance honestly: this is game evidence, not a fabricated completed Study attempt.

Apply each logical game/decision effect once. A subsequent genuine Study review updates learning state and settles the corresponding obligation through the same owner. Preserve occurrence time separately from processing time; late imports must not rewind newer learning state. The precise late-evidence policy is a bounded implementation choice, not a prerequisite for the first recent-game case.

An actual covered encounter can justify first introduction whether the expected move was played or missed. Study rechecks current admission and the exact approved mapping; first introduction uses fresh scheduling, with no fake Again, completed review, or backdated introduction. Reuse the same logical game/decision/occurrence identity so later introduction cannot turn the old event into a lapse.

Persist deduplicated historical importance separately from lapse eligibility for both introduced and unintroduced decisions, including misses before introduction or followed by later learning. Correct encounters supply frequency evidence. Do not rewind later learning or reopen settled debt as a new lapse.

### G03 — Availability is not completion

The card must become available for Study within **1–2 days of detecting/admitting the qualifying miss**, even when Andy does not study. Do not interpret the requirement as a promise that he completes a review in that period.

The obligation survives days without Study, queue rebuilds, restarts, and retries. Daily introduction limits must not silently erase it. Recover overdue obligations when the application resumes. An unrelated active attempt is never replaced underneath the learner.

Encounter first introductions use the same availability goal with honest safety holds. The approved targeted exception bypasses ordinary new-card caps, including zero allowance, and Study-only without changing global settings or ordinary introductions. Full manual pause and foreground, maintenance, security, source, legality, and conflict guards remain effective; report deferral honestly rather than promising availability while held. Use bounded, idempotent batches, not an invented reserve cap.

### G04 — Optional producers fail independently

Provider outages, unavailable engines, and failed background jobs must not stop, restart, or block normal Study or tactics. Show pending/failed work and allow retry. Background scheduling controls when jobs run; it does not independently set learning due dates. Bound optional computation and database work so that it does not monopolize Study resources.

## 4. Repertoire identity and integrity

### P01 — Exact position identity

A repertoire position key uses board arrangement, side to move, castling rights, legally relevant en passant, and variant, scoped to the repertoire. Ignore move clocks for ordinary repertoire lookup; retain full FEN and relevant history for evaluation. Exact transpositions share position identity. Approximate similarity never establishes identity or authorizes merging unrelated cards or histories.

### P02 — One own response, with enforceable conflicts

At a position where Andy moves, each repertoire has at most one selected response. Different repertoires may choose differently. Opponent branches remain unrestricted. Multiple valid tactic solutions are a separate matter and are not prohibited by this rule.

Audit existing content and validate imports and accepted edits. Show competing moves and affected routes. Block the affected repertoire Study occurrences until Andy explicitly resolves the conflict. Do not silently choose a winner, prune branches, or treat competing own responses as accepted alternatives.

Preserve evidence and unrelated memberships. If a newly detected conflict invalidates an active attempt's target, suspend that affected attempt without manufacturing a recall failure; unrelated attempts continue.

## 5. Coverage, introductions, and repairs

### S01 — Likelihood evidence

Use Explorer first; add Maia through the same probability-provider boundary. Choose the rating population and time controls before using likelihoods for ranking. Store provider, settings, observation date, and sample counts where available; mark unavailable counts rather than inventing them.

Preserve missing/unknown probability mass. Do not renormalize a partial Explorer response or a subset of Maia moves into apparent full coverage. Rank gaps using both position reach and opponent-reply likelihood. Personal games should update encounter priorities without erasing population provenance.

### S02 — Probability-driven introductions

Rank eligible, approved, unintroduced opening decisions by expected encounter likelihood, evidence support, and uncertainty—not a statistical-significance test. Deduplicate shared decisions and transpositions. Required reviews and admitted game misses take precedence over discretionary new introductions.

For discretionary introductions, use one global opening introduction budget with user-selected relative repertoire weights. G03 governs the real-game encounter exception. Depth should follow likely useful encounters rather than requiring every ancestor to reach an arbitrary maturity threshold. Changes to this policy belong to Study's introduction owner; optional analysis supplies rankings, not direct queue writes. Retain the last usable ranking or the existing safe order when evidence is unavailable.

### S03 — Familiar candidates first, engine-checked in the actual position

For a gap or inconsistent response:

1. Look for an exact repertoire continuation, then comparable repertoire positions.
2. Retrieve familiar responses and check their legality in the target position.
3. Evaluate those candidates alongside the engine's preferred move; do not restrict familiarity to the engine's initial top-five list.
4. Prefer a familiar response that meets the configured acceptability policy.
5. Present a proposal with supporting examples, evaluation tradeoff, and uncertainty.

The default maximum loss is **30 centipawns**, evaluated from Andy's perspective: `loss_cp = best_evaluation_cp - candidate_evaluation_cp`, using comparable settings and position context. Record engine version, depth/budget, candidate score, best score, and threshold. Any context-specific adjustment must be explicit and consistent; do not silently widen the threshold to approve a proposal. Handle mate separately. Missing or inconclusive evaluation is not acceptance evidence.

A repertoire-wide consistency audit applies this process to already-covered positions as well as gaps. Explain what makes the examples similar and where they differ. Count distinct positions rather than repeated copies of the same line as independent support.

### S04 — Common-blunder preparation

Identify frequently played opponent errors and an effective response using frequency and engine evidence. The branch may already be covered. Surface its existing unintroduced or under-practiced response, or propose an addition when needed. Keep the opponent-blunder criterion separate from the 30 CP own-candidate tolerance.

### S05 — Deliberate content changes

Provide accept, edit, and dismiss actions. Revalidate edits and the one-own-response invariant before publication. Acceptance must show both the repertoire change and any associated new cards; accepting that proposal authorizes those displayed additions. No unapproved repertoire edits or new training content.

R11's automatic regrouping of already-approved moves is not a repertoire edit or an introduction of new content.

## 6. R11 — Adaptive card recombination

### C01 — Earn eligibility through clean recall

Each opening training presentation has its own consecutive-clean-review counter. A genuine, complete, unassisted correct review increments it once. A wrong response, Again, hint/reveal, or guided completion is not a clean success and resets the streak. Incomplete attempts do not increment it. Retries of one submission cannot count twice.

**Three consecutive clean reviews make the presentation eligible to join.** Count distinct completed Study reviews, not individual moves, corrections, or teaching retries. Distinct calendar days and a seven-day cooldown are not required by the initial policy. Real-game successes do not fabricate Study successes.

### C02 — Join compatible neighbours automatically

Both participating cards must be eligible. Prefer reconnecting an isolated continuation to its recorded parent; otherwise require verified adjacency along an authored route. Preserve learner color, repertoire scope, variant, exact connecting positions, and intervening opponent cues.

Do not concatenate alternative opponent branches, invent bridging moves, or select an ambiguous route silently. The first implementation joins unambiguous route-local neighbours. A shared card's other repertoire/branch uses remain valid.

Apply a join automatically **between attempts**, after saving the current review. Do not alter an active presentation. Replace duplicate separate queue presentations only for the combined route; keep content, component identities, review records, and split/join lineage.

### C03 — A combination must prove itself

The combined presentation starts at **0/3**, regardless of the component streaks. A clean review of the entire sequence counts once. After three clean complete reviews, it may join another eligible adjacent presentation. There must be no immediate chain of joins based on inherited streaks.

Example: `A(3/3) + B(3/3) → AB(0/3)`; later `AB(3/3) + C(3/3) → ABC(0/3)`.

The combined material remains introduced and consumes no new-card allowance. Study owns an explicit conservative schedule for the new presentation; do not pretend separate successes were historical reviews of the combination or silently average/copy FSRS state. Game-feedback obligations must continue to target the relevant decision/current presentation without disappearing, duplicating, or losing their availability deadline.

### C04 — Preserve the way back to smaller cards

Retain the existing repeated-failure split behavior for a combined sequence. The reviewed implementation offers a split after three Again outcomes among the last five attempts and requires acceptance. Its first reuse shortens the prefix and isolates the final learner decision; targeting a different failed decision is a separate improvement, not an implied existing feature.

A failure resets joining eligibility. Splitting must not immediately rejoin the same fragments using pre-failure streaks. Preserve history and require fresh clean evidence before recombination.

Implement this as a bounded Study presentation feature, not a new segmentation service or a mandatory per-decision scheduling migration. If excessive same-day split/rejoin cycles emerge, consider a cooldown as a later policy change rather than a prerequisite now.

## 7. Ownership and product surfaces

Logical modules need not be separate services, deployments, or databases. Use narrow typed interfaces, explicit state/effect ownership, and immutable detector inputs. Prefer cohesive classes for client/lifecycle state and pure functions for calculations; avoid a central coordinator that owns every policy.

| Owner | Responsibility |
| --- | --- |
| Repertoire | Approved moves, routes, position index, proposals, and the one-own-response invariant. |
| Study/Learning | Learning effects, due state, priority obligations, introduction selection, reviews, and split/join presentation changes. |
| Tactics | Existing capture, validation, collection/pack controls, solving, and Study submission. |
| Provider adapters | Separate Lichess and Chess.com acquisition; isolated provider failures. |
| PGN normalizer and comparer | Typed games and pure, provenance-bearing repertoire findings. |
| Game-feedback coordinator | Import/comparison/admission workflow and retries through owning interfaces. |
| Background-job runner | When optional jobs execute, checkpoints, and bounded retries—not learning schedules. |
| Probability providers | Explorer/Maia likelihoods and their provenance. |
| Similarity matcher | Comparable positions and candidate responses—not identity or move-quality decisions. |
| Stockfish evaluator | Bounded candidate evaluation and evidence—not content edits. |
| Suggestion coordinator | Combines findings and evidence into proposals; Repertoire applies approved changes. |

Keep **Study**, **Tactics**, **Game review**, **Repertoire**, and small **Settings**. Coverage, conflicts, similarity, and repairs belong within Repertoire. Reuse the shared board and existing useful UI. Retire an old view only after preserving its useful capability elsewhere; simpler navigation is not permission to remove working behavior.

## 8. Delivery and bounded acceptance

Deliver through the current product, one useful slice at a time. R11 is a separate Study slice and does not block game feedback. Exact-conflict work can proceed independently. Maia refines a useful Explorer-based path; engines are unnecessary for the first game miss.

The immediately next practical bundle reuses the first matching, evidence, delivery, and Study path for bounded already-imported history, first introduction, and historical importance. Full synchronization, new UI, and engines are not prerequisites; existing approved repertoire content needs no new engine evaluation. Target acceptance reports scanned/matched/unresolved/remaining facts and correct versus missed encounters, demonstrates actual first admission with ordinary allowance zero and Study-only on, full-pause deferral, preserved later learning, and no duplicate effect on resume. These are required proofs, not claims of installation.

| Slice | Observable acceptance |
| --- | --- |
| A — One game to Study | **A01:** One already-imported real miss maps to the actual existing introduced card, becomes available within D01, and receives D02's learning effect. **A02:** Duplicate replay has one effect; an unrelated active attempt survives; the obligation remains available across an off-day/restart. |
| B — Continuous game feedback | **A03:** Both providers import incrementally without silent truncation. Gaps, unintroduced cards, conflicts, ambiguous identities, and route reentry are classified correctly. **A04:** Provider failure leaves Study/Tactics usable and work retryable. |
| C — Exact repertoire audit | **A05:** A same-position own-response conflict blocks affected Study, is resolved manually, and leaves opponent branches and other memberships intact. Detectors do not mutate inputs. |
| D — Introductions | **A06:** With other eligibility equal, a 20%-reach decision precedes a 2%-reach decision; a transposed duplicate gets no extra slot. Personal evidence can change ordering; budget/weights and unavailable-provider fallback are respected. |
| E — Repairs and common blunders | **A07:** Show a familiar legal candidate checked against the 30 CP policy, plus supporting positions and provenance. No content changes before approval. Demonstrate both a missing reply and a common-blunder response that was already covered. Maia may follow independently. |
| F — Connected practice | **A08:** Eligible A/B join to AB at 0/3; AB cannot join C until three new clean reviews. Failure resets eligibility, repeated misses retain split eligibility, and no active attempt/history/obligation is lost. |

Acceptance is attached to the changed behavior. Use focused owner tests and the smallest relevant integration demonstration. Preserve retry safety, history, active-attempt protection, and permission boundaries. Do not require a full migration, whole-repository cleanup, or repeated full-system testing for an unrelated feature or documentation-only edit.

Existing actual required checks are not silently waived. If an obsolete gate no longer matches the delivery policy, revise it explicitly and narrowly. Reuse unchanged evidence when applicable; rerun when executable inputs or affected contracts change. Report safe, non-blocking limitations with their fallback rather than treating speculative edge-case closure as the definition of completion.

## 9. Decisions and remaining choices

| ID | State | Policy |
| --- | --- | --- |
| D01 | Decided | Availability within 1–2 days, including days without Study; not forced completion. |
| D02 | Decided outcome | A game lapse has at least the impact of Study Again. Default: owning scheduler's lapse transition plus a durable priority obligation. |
| D03 | Decided | Strict one-own-response rule within a repertoire; affected material is blocked until manual resolution. |
| D04 | Open for likelihood ranking | Select rating band and time controls. Does not block game feedback or exact audits. |
| D05 | Corrected and decided | Default candidate tolerance is **30 CP, not 300 CP**. Record any explicit contextual policy. |
| D06 | Decided | Manual acceptance of repertoire changes and their displayed associated additions. R11 changes presentation only. |
| D07 | Decided | Resolve own-response conflicts manually; preserve evidence and unrelated content. |
| D08 | Open correction policy | Define corrected/withdrawn-game handling. Bounded proposed default: quarantine changed evidence, revise/cancel unapplied obligations, and do not automatically reverse historical learning effects. |
| D09 | Agreed recombination policy | Three consecutive clean reviews per participant; automatic safe-boundary joins; new combination starts at zero; existing repeated-failure splitting remains. |
| D10 | Delegated implementation detail | Define conservative combined-presentation scheduling and late-import ordering within Study ownership. No invented combined successes or new scheduling authority. |

Do not turn a locally unresolved parameter into a blocker for independent slices. Do not silently make D04 or D08 a user decision by selecting an undocumented value.

## 10. Concrete reuse map

This map records candidates from the source review at `24ec4c06f666f50104c979840c403826fb1ce280`; it is not installed-runtime certification. Follow the current owning public interfaces when implementing. Reuse is optional when extraction would recreate the old orchestration cost.

| Piece | Specific existing source | Treatment |
| --- | --- | --- |
| Working Study/Tactics | [Home](app/views/home_view.tsx), [TrainingView](app/views/training_view.tsx), [TacticsView](app/views/tactics_view.tsx) | Keep in place. Do not copy their entire application controllers into a replacement frontend. |
| Board | [Board UI exports](packages/board-ui/src/index.ts) | Reuse for the two new review surfaces. |
| Lichess / Chess.com | [Lichess adapter](backend/app/games/providers/lichess.py), [Chess.com adapter](backend/app/games/providers/chesscom.py) | Reuse acquisition functions; coordinate completeness/checkpoints outside the adapters. |
| PGN normalization | [game-ingestion](packages/game-ingestion/src/tempo_game_ingestion/__init__.py) | Reuse `PgnGameIngester` and typed inputs. |
| Repertoire comparison | [comparison](packages/repertoire-comparison/src/tempo_repertoire_comparison/comparison.py), [frontier](packages/repertoire-comparison/src/tempo_repertoire_comparison/frontier.py) | Reuse traversal; add the agreed classifications/reentry behavior. |
| Exact identity / conflicts | [identity](packages/evidence-math/src/tempo_evidence_math/_identity.py), [conflict scanner](backend/app/studies/repertoire_conflicts.py) | Reuse pure logic; expose invalid-input diagnostics and enforce blocks through the owning service. |
| Scheduling / feedback | [scheduler](packages/scheduling/src/tempo_scheduling/_scheduler.py), [current-owner evidence routes](backend/app/game_evidence_routes.py), [remediation adapter](backend/app/services/legacy_study_remediation.py) | Reuse Study scheduling and admission. Current bridge still needs the lapse effect and durable later-day admission; replace narrowly if integration becomes a framework exercise. |
| Explorer / reach math | [Explorer provider](services/intelligence/src/tempo_intelligence/provider.py), [move probabilities](packages/evidence-math/src/tempo_evidence_math/_move_probability.py), [policy reach](packages/evidence-math/src/tempo_evidence_math/_policy_reach.py) | Reuse adapters and calculations without requiring the old Intelligence runtime. Write the small introduction-selection policy. |
| Familiarity | [discovery admission](backend/app/services/discovery_admission.py) | Extract comparable-position helpers; do not import the old opportunity orchestration. Evaluate familiar candidates even outside engine top-five output. |
| Stockfish | [process adapter](scripts/stockfish-process.mjs), [search wrapper](scripts/engine-search.mjs) | Reuse execution, separate old polling/leases, or replace with a small adapter if cheaper. |
| Maia | [model](workers/maia/model.mjs), [core](workers/maia/core.mjs) | Reuse inference/assets. Request all legal moves for coverage; subset normalization is not full-board probability. |
| Tactic capture | [capture owner](backend/app/services/tactic_capture.py), [puzzle PGN parser](app/lib/tactic-capture-pgn.ts) | Preserve existing capture API and content. Do not rebuild tactics to deliver game feedback. |
| Split/rejoin | [prefix split](backend/app/services/prefix_split.py), [split commands](backend/app/prefix_split_commands.py) | Reuse legal split/lineage concepts and the current owner; implement the missing reverse join. The old [adaptive design](docs/ADAPTIVE-OPENING-SEGMENTATION.md) is reference, not a required rollout. |

New product work is principally the small import/feedback coordinator, a reusable repertoire-position index, introduction selection, reviewable proposal orchestration, two review surfaces, and the R11 join operation. No new per-module services or blanket database redesign are implied.
