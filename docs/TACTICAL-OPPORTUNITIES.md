# Tactical-opportunity model

This document defines the first iteration of tactical-opportunity statistics in
Tempo. The model extends the per-move evidence in `game_move_analysis` and the
durable, reviewable records in `game_findings`; it does not introduce a second
analysis pipeline.

## Scope and analyzed side

The analyzed side is the Tempo player's side, `imported_games.color`. Engine
scores are normalized to that side before any comparison. A decision position is
the position immediately before a row whose `game_move_analysis.is_player_move`
is true and whose `mover_color` matches the player's color. Opponent moves are
useful evidence for how an opportunity was created, but are not opportunities
credited to the player.

The default eligible-game filter is:

- an imported, legal standard-chess game with a known player color;
- rated play with speed `blitz`, `rapid`, or `classical` (the same supported
  game-analysis job filter);
- `analysis_state='ready'`, with a completed analysis version; and
- `adaptive_excluded=0` for aggregate statistics and recommendations.

The report period, provider, username, speed, and result may further narrow this
set. No post-repertoire or post-out-of-book restriction is implicit: in-book
player decisions remain eligible unless the caller explicitly requests a
post-book report.

An eligible position must have a legal best move, a usable engine score, and a
principal variation that can be replayed far enough to establish the motif and
its consequence. Positions with missing, truncated, or illegal engine output
are unscored and are excluded from both the numerator and denominator. A
position is counted once for its selected top-level motif, even when several
engine lines or acceptable moves demonstrate the same opportunity. Additional
candidate motifs remain in evidence for review until multi-label statistics are
defined; they do not silently multiply the conversion denominator.

## Thresholds and comparison rules

The first-iteration defaults are:

- **Tactical consequence:** at least 300 cp of net material gain in the
  analyzed side's favor, or at least 100 cp of engine-evaluation benefit for a
  concrete motif continuation, or a forced-mate improvement. These alternatives
  are intentionally disjunctive: a clean winning exchange can qualify by
  material, while a forcing attack can qualify by evaluation or mate.
- **Missed-opportunity loss:** 100 cp, matching the current default
  `major_mistake_cp` setting. A position must lose at least this much, from the
  best available continuation to the played move, to be labeled `missed`.
- **Acceptable-move tolerance:** 30 cp from the engine best move, matching the
  current default `engine_line_window_cp`. All moves inside that window are
  acceptable candidates; they are not separate opportunities. The existing
  gameplay-event classifier's hard-coded 50 cp window is legacy behavior; new
  opportunity statistics use the configured 30 cp default.
- **Mate comparison:** a forced mate for the analyzed side is better than any
  finite score, and a forced mate against that side is worse than any finite
  score. An acceptable move must preserve the same mating side and remain
  within two plies of the best mate distance. Losing the forced mate, changing
  its side, or exceeding that distance is outside the acceptable set.

For a centipawn consequence, `pv_benefit_cp` is the player-oriented score at
the first stable post-consequence position in the best line minus the
player-oriented score at the decision position. It is not the raw root
evaluation: a position being favorable already is not itself a tactical gain.
The first stable post-consequence position is after the forcing capture,
deflection, or checking sequence has resolved and the opponent has had the
engine's best reply. If that point cannot be identified, the position must
qualify by material or mate instead. For a player move, evaluation loss is
`loss_cp = player_score_before - player_score_after`, with mate ordering applied
before any finite-cp arithmetic.

The thresholds are configuration, not magic numbers. Every finding records the
thresholds and analysis metadata used to produce it so later recalculation can
be distinguished from a historical result.

## Opportunity states

An **available** opportunity exists at a player's decision position when the
engine's best principal variation contains a tactically relevant motif and
demonstrates one of the tactical consequences above. The evidence must be
concrete in the line: geometry alone is insufficient.

An opportunity is **exploited** when the player's played move is in the
acceptable-move set, including a different move within the cp tolerance, or
when it preserves the specified mating result within the mate-distance
tolerance. The move need not be textually identical to the engine's first
choice and need not carry the same motif label if it preserves the concrete
gain.

An opportunity is **missed** when it was available before the move, the played
move is outside the acceptable set, and the resulting player-oriented loss is
at least the missed-opportunity threshold. An available opportunity that is
not acceptable but loses less than that threshold is not persisted as a tactical
opportunity in this first iteration.

The conversion rate is:

```text
exploited opportunities / all available opportunities
```

Reports always show raw `opportunities`, `exploited`, and `missed` counts beside
the rate. Thus small samples are visible and zero denominators render as
undefined rather than a misleading percentage.

## Motif identifiers

The stable top-level identifiers for the first iteration are:

`pin`, `fork`, `skewer`, `discoveredAttack`, `hangingPiece`, and
`matingTactic`.

Mating tactics may carry a subtype such as `backRankMate`, but statistics use
the stable `matingTactic` category unless a report explicitly asks for
subtypes. A pin always retains top-level identifier `pin` and additionally
records `pin_type` as one of:

- **absolute:** moving the pinned piece would expose its king to check;
- **relative:** moving the piece exposes a more valuable target, but does not
  expose the king; or
- **situational:** the piece is constrained by the concrete position and line
  (for example, a defender cannot move without allowing a forcing tactical
  sequence), without satisfying the strict absolute or relative geometry.

The `pin_type` is descriptive evidence, not a separate top-level opportunity.
For every motif, classification requires that the principal variation show the
resulting capture, material win, forced check/mate, or qualifying evaluation
consequence. When a line supports more than one category, the existing
classifier's deterministic primary motif is the one counted and all candidates
remain in evidence. A pinned enemy piece that remains harmless is not a pin
opportunity.

The documented primary-motif precedence is
`matingTactic > pin > fork > skewer > discoveredAttack > hangingPiece`.
Precedence only chooses the legacy single `motif` column; the structured
evidence list retains every matching detector result.

## Examples

1. **Irrelevant pinned piece.** The best move leaves an enemy knight pinned to
   its king, but the principal variation never attacks or wins that knight and
   produces no qualifying material, evaluation, or mate consequence. The
   geometry is recorded, if useful for debugging, but no `pin` opportunity is
   counted.
2. **Winning pin created by the best line.** A rook move pins an enemy queen
   to its king; the replayed line forces the queen to remain on the line and
   wins at least a minor piece or meets the evaluation threshold. The position
   has an available `pin` opportunity. A player move inside the acceptable
   set is exploited; a move that drops the gain and loses at least 100 cp is
   missed.
3. **Pre-existing pin exploited.** The position already contains an absolute
   or relative pin. The best line captures or otherwise converts the pinned
   piece for a qualifying gain. Playing any acceptable conversion counts as an
   exploited `pin`; the pin does not need to be created on the current move.
4. **Pre-existing pin missed.** The same concrete conversion is available, but
   the player makes a different move outside the tolerance and the evaluation
   falls by at least 100 cp. This is an available and missed `pin` opportunity.

## Counting and lifecycle rules

- **Transpositions:** count each eligible decision occurrence the player
  actually faced. Transposed positions on different plies are not merged,
  because each occurrence is a separate learning event. Multiple PVs and
  multiple acceptable moves for one occurrence still produce only one count
  for that motif.
- **Multiple acceptable moves:** store or derive the complete acceptable set,
  and classify the played move against the set. Never multiply counts by the
  number of equivalent moves.
- **Already-winning positions:** a favorable root evaluation alone is not an
  opportunity. The line must add a qualifying concrete tactical consequence.
  A genuine mating conversion or additional material/evaluation gain can still
  qualify even when the position was already winning.
- **Incomplete engine output:** do not infer a miss from absent evidence. Leave
  the position unscored, retain the engine failure/coverage diagnostic, and
  exclude it from the opportunity denominator.
- **Analysis versions:** opportunity records are tied to the game
  `analysis_version`, engine version, network version, and threshold snapshot.
  A new analysis version replaces the active derived result for that version;
  versions are never mixed into one rate. Comparisons across versions must be
  labeled as comparisons, not presented as one continuous statistic.

The persisted opportunity uses the additive `tactical_opportunities` table;
misses additionally use the existing `game_findings` path with
`kind='tactical miss'` and a source-opportunity link. Its evidence should include the decision FEN,
top-level motif, `pin_type` when applicable, best move and acceptable moves,
replayed principal variation, player-oriented scores, consequence type and
amount, outcome, thresholds, and analysis-version metadata. This keeps cards,
finding decisions, and motif recommendations backed by the same auditable
source.
