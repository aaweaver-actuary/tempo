# Tempo product specification

Version 0.2 · October 26, 2026 · Single-user, local-first product

First-departure encounter amendment · October 30, 2026 · Approved targets, not installation claims.

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
| R02 | Import Lichess and Chess.com games; select the first departure from the applicable authored repertoire. For an own departure with an exact eligible target, apply ordinary Study Again once if introduced at first authoritative acceptance, or canonically introduce an unseen target. Issue the introduced target at the next safe unissued head and the isolated eligible unseen live target within five new issuances. |
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

### Historical collection-first checkpoint and current feedback delivery

The October 29 collection-only release collects explicitly configured games continuously and retains
honest per-repertoire deviation evidence. Automatic Study delivery stays disabled;
original pending commands remain durable and unattempted. Missing comparisons are
unresolved until a later reconciliation obtains the repertoire source. A bounded
collection-only proof and inactive installation do not establish Study priority or
settlement acceptance. #594/#595, engines, coaching, ranking and new UI are later
work at that checkpoint. Live collection requires Andy's explicit switch-on instruction.

The October 30 assignment advances #624 through implementation, packaged proof,
installation and authorized feedback activation under G01–G04 below. Preserve all
saved provider/repertoire scope and Study history. The existing Game Review screen
shows the retained global first departure, its pre-move position, expected/played
responses and actual Study progress. Per-repertoire findings remain diagnostics.
Missing currentness evidence is explicitly unknown. Corrected scope may hold an
obsolete original effect while recording a different legitimate occurrence;
unchanged games remain inspectable and original effect clocks/history survive.
Collection-only remains a supported fallback which stops new Study delivery.

The [collection operator](scripts/README.md#games-collection-operator) reports
installed identity and missing inputs before enabling only its own worker.

Explicit **Refresh now** requests fetch Chess.com or Lichess games through the
current time using saved scope and the last completed checkpoint, with the
existing two-day lookback. They run once on the existing background worker even
when recurring collection is off. Exact-job progress and reload recovery belong
in Games. This explicit action does not enable recurrence or Study delivery;
foreground priority, Study-only pause and provider retry delays still apply.


### G01 — Select one first repertoire departure

Separate provider adapters fetch games into one typed PGN representation. Preserve provider/game identity, player color, game time and its precision, move history, and import provenance. Import incrementally with retry-safe checkpoints. A bounded provider response must not silently truncate a requested date range.

A pure selector legally replays the normalized game across all configured, applicable same-color authored routes. Any surviving authored alternative covers the played move; a different repertoire's expected response cannot manufacture a miss. Select the earliest provable unsupported move once, retaining source, pre-move position, one-based game ply, actor, expected alternatives and relevant scope. A miss means outside that authored repertoire, not low engine quality or proof of historical forgetting. Engines, Maia, Explorer and introduction-before-game chronology are unnecessary.

An own first departure can create a Study consequence only with complete relevant scope and one exact approved current decision/physical presentation mapping, including an interior decision of an existing multi-move card. Equivalent route aliases collapse deterministically; different responses or physical targets remain ambiguous. A producer label, FEN equality or partial single-repertoire prefix proof cannot grant this authority. Study revalidates current source, configuration, publication, route and target facts through the owning typed boundary.

An opponent first departure is retained as a repertoire gap and produces no Study consequence from this or any later move in that game. Deliberate coverage end, missing own continuation, ambiguous mapping, incomplete/unavailable scope, illegal/unsupported source and unavailable current authority retain their specific non-actionable or unresolved outcomes; they never invent an expected move/card or a failure. An ended or unavailable competing route cannot be discarded to manufacture uniqueness. Fully covered play and correct encounters may retain adherence/frequency evidence but create no urgent introduction or failure under this policy. Later diagnostic reentry remains useful evidence without authorizing another effect.

The #615 explicit size-only acknowledgment supports collection-only partial comparison. A deferred repertoire remains unknown, not empty; complete selected-scope feedback requires #616's supported source read.

### G02 — One learning owner; a real learning effect

Study/Learning owns schedules, review effects, priority, queue admission, and settlement. Game feedback submits a typed command through that owner; it never writes learning tables or becomes a second scheduler.

Current Study state at first authoritative acceptance fixes one immutable disposition for the occurrence. An introduced own-miss target receives the existing ordinary scheduler's **Again** transition once at actual processing time and the authoritative current Study day, plus a durable urgent obligation. Use the existing `game_lapse` scheduling source and actual scheduling review receipt. Keep the game occurrence time separately. Do not backdate, clear introduction/history, fabricate a human completed attempt, or waive a newly accepted reset because a clean review occurred after the game but before acceptance. Human completion counts remain distinct from game scheduling events; normal failure/recombination consequences still apply.

An unseen own-miss target receives canonical first introduction with fresh scheduling and no fake Again/completed review. That accepted intent survives a racing ordinary introduction and later retries. The exact approved target uses the encounter exception to ordinary allowance, including zero/exhaustion, and Study-only without changing those global settings. Correct play supplies frequency evidence and does not create urgent first introduction through this feature.

The logical key is `(workspace, provider, game_id, decision_id, occurrence_ply)`. Source/policy/comparison revisions, retries and queue days do not create another event. Preserve original command bytes/digest, occurrence/detection facts, accepted disposition and separate qualification metadata. Changed payload under one key is a conflict/control case. Distinct genuine games remain distinct obligations even when they share one physical queued target.

Preserve a genuine already-issued attempt and its save/replay. If it prevents the reset, keep the reset owed, save that attempt normally, then apply Again and require a subsequent compatible issued generation. Introduced-Again settlement requires its durable scheduler receipt plus an authentic clean post-reset issued attempt and saved review receipt; a pre-reset attempt completing after acceptance cannot settle it. First introduction instead requires canonical admission/current-presentation evidence and an authentic clean post-admission issued attempt, with no scheduler-review prerequisite. Frequency-only evidence has no remediation debt.

Qualify frozen collection commands against the new first-departure policy without rewriting their original keys or bytes. Suppress later/matched/per-repertoire false candidates; explicitly hold unavailable authority and deliberately handle legacy lapse commands. Outstanding old priority-only effects, including acknowledged Games deliveries, need one versioned Study-owned qualification/application transition: `applied_at` alone proves no Again. Preserve settled/withdrawn history and existing control fences; source corrections never erase genuine later learning or replay a settled effect under a new key.

### G03 — Measure actual issuance from actionable determination

Start service measurement at durable actionable determination: source and complete relevant comparison are known and an exact candidate target has been resolved. Preserve that original timestamp through preparation, delivery, acceptance and maintenance. Count distinct newly authorized Study attempts after it across tabs, sessions, refreshes and day rollover. Prefetch, reload, queue presence and the already-issued current attempt do not count. Actual target issuance is the endpoint; acceptance, priority, admission or an `available` badge alone is insufficient.

With no earlier live urgent backlog, an eligible introduced target is the next safe newly issued head after Again; an isolated eligible unseen live target is actually issued within the next five new issuances, preferably immediately once ready. Preserve genuine issued work while moving ordinary unissued cached successors behind ready urgency. Continuous foreground Study must progress through bounded local preparation/intake at safe command boundaries, without waiting indefinitely for a quiet window or moving provider I/O/full repertoire replay into the short Study writer.

More than five distinct simultaneous eligible targets cannot all fit in five positions. Prefer live urgent work over discretionary historical draining, retain oldest actionable determination order within each class with stable ties, bound historical intake, and expose actual backlog/missed service bounds. Preserve eventual historical presentation when capacity exists or live backlog is finite; fair intake alone does not prove presentation. Use forward/reconciliation/manual provenance, with no arbitrary age cutoff forgiving qualifying games. A measured preparation delay remains within the original interval.

Explicit full manual pause, maintenance, source uncertainty, burial or current ineligibility is an honest visible hold. Ordinary allowance zero and Study-only are not holds for targeted encounter introduction. Outstanding debt survives off-days, queue rebuilds, retries, worker shutdown, expired leases and missed service targets; a deadline is not a TTL. Use the same authoritative Study day for scheduler, queue and issuance. Accepted obligations remain Study-owned even when optional Games stops.

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

Rank eligible, approved, unintroduced opening decisions by expected encounter likelihood, evidence support, and uncertainty—not a statistical-significance test. Deduplicate shared decisions and transpositions. Among reorderable queue slots, admitted urgent game misses come first, then eligible, admitted, untouched new repertoire cards in their frozen relative order, then ordinary reviews and other ordinary work. Apply this presentation order on daily construction, safe refresh after new-card admission, and adoption by an existing ready day. Active, issued, presented or teaching attempts, explicit work, unavailable rows and positioned retries retain their protected positions.

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

The first-departure delivery bundle reuses collection and the owning Study path for introduced Again, unseen introduction and retained history. #618 freezes shared semantics; #619–#623 deliver components, #581 supplies compatible canonical unseen admission, and #624 owns the assembled ordinary Study and installed feedback checkpoint. The earlier collection-only checkpoint remains valid and inactive until its authorized operator enables it; it proves no feedback activation. New UI and engines are unnecessary. Acceptance distinguishes determination, acceptance, scheduling/admission, actual issuance and genuine settlement, with preserved history and no duplicate effect on resume.

| Slice | Observable acceptance |
| --- | --- |
| A — One game to Study | **A01:** An exact own first departure receives ordinary Again and next-safe-head issuance if introduced, or canonical introduction and actual issuance within D01 if unseen, with ordinary allowance zero and Study-only on. **A02:** Replay has one effect; an already-issued target saves normally without settling an unapplied reset, and a genuine subsequent compatible completion settles the debt. Off-day/restart preserves outstanding obligations. |
| B — Continuous game feedback | **A03:** Both providers import incrementally without silent truncation; surviving authored alternatives cover valid moves, opponent-first gaps block later effects, and retained mixed work is qualified under one stable key. **A04:** Continuous Study advances determined work without a quiet window; optional provider/worker failure leaves Study/Tactics issuing/saving and accepted obligations recoverable. |
| C — Exact repertoire audit | **A05:** A same-position own-response conflict blocks affected Study, is resolved manually, and leaves opponent branches and other memberships intact. Detectors do not mutate inputs. |
| D — Introductions | **A06:** With other eligibility equal, a 20%-reach decision precedes a 2%-reach decision; a transposed duplicate gets no extra slot. Personal evidence can change ordering; budget/weights and unavailable-provider fallback are respected. |
| E — Repairs and common blunders | **A07:** Show a familiar legal candidate checked against the 30 CP policy, plus supporting positions and provenance. No content changes before approval. Demonstrate both a missing reply and a common-blunder response that was already covered. Maia may follow independently. |
| F — Connected practice | **A08:** Eligible A/B join to AB at 0/3; AB cannot join C until three new clean reviews. Failure resets eligibility, repeated misses retain split eligibility, and no active attempt/history/obligation is lost. |

Acceptance is attached to the changed behavior. Use focused owner tests and the smallest relevant integration demonstration. Preserve retry safety, history, active-attempt protection, and permission boundaries. Do not require a full migration, whole-repository cleanup, or repeated full-system testing for an unrelated feature or documentation-only edit.

Existing actual required checks are not silently waived. If an obsolete gate no longer matches the delivery policy, revise it explicitly and narrowly. Reuse unchanged evidence when applicable; rerun when executable inputs or affected contracts change. Report safe, non-blocking limitations with their fallback rather than treating speculative edge-case closure as the definition of completion.

## 9. Decisions and remaining choices

| ID | State | Policy |
| --- | --- | --- |
| D01 | Decided | From durable actionable determination: introduced target at the next safe unissued head after Again; isolated eligible unseen live target by the fifth new issuance. G03 states holds, capacity, history and durable overdue recovery. This measures actual issuance, not forced completion or a 48-hour wait. |
| D02 | Decided outcome | Exact own first departure: current-state ordinary Again once when introduced at first authoritative acceptance, canonical fresh introduction when unseen, and a durable urgent obligation. No introduction-before-game gate, fake human review or priority-only substitute for Again. G02 owns identity/disposition/settlement semantics. |
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
