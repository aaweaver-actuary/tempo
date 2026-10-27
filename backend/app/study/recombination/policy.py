"""Pure C01–C04 policy. The Study application owns ordering and all effects."""
from __future__ import annotations

from dataclasses import dataclass, replace

import chess
from tempo_evidence_math.interfaces import opening_position_key
from app.contracts.product_v02.presentation import OpponentCue, Presentation

from .models import (
    AuthoredRouteFacts, CleanStreak, ComponentRef, JoinDecision, JoinHold,
    JoinEvidence, JoinMember, RetainedUse, ReviewEvent, RouteContext, RouteJoinPlan, RouteUse,
)


def reduce_clean_streak(state: CleanStreak, event: ReviewEvent) -> CleanStreak:
    """Reduce one owner-ordered fact; old content/split generations cannot count.

    The owner admits/chronologically places game lapses before calling this
    function. Raw game results never change Study eligibility. Unknown historical
    assistance is not clean evidence. Completed attempts/reviews are terminal;
    revised historical facts require owner-ordered replay, not another submission.
    """
    if (event.presentation_id, event.presentation_revision, event.evidence_epoch) != (
        state.presentation_id, state.presentation_revision, state.evidence_epoch
    ) or event.event_id in state.seen_event_ids:
        return state
    if event.source == "real_game":
        return state
    if event.source == "study" and (
        event.attempt_id in state.completed_attempt_ids
        or (event.review_id is not None and event.review_id in state.completed_review_ids)
    ):
        return state
    if state.last_event_at is not None and event.occurred_at < state.last_event_at:
        raise ValueError("out_of_order_event: Study must place admitted evidence chronologically")
    next_state = replace(
        state, seen_event_ids=state.seen_event_ids | {event.event_id}, last_event_at=event.occurred_at
    )
    failed = (event.source == "admitted_lapse" or event.outcome != "correct"
              or event.assistance == "assisted")
    if failed:
        return replace(
            next_state, count=0,
            tainted_attempt_ids=state.tainted_attempt_ids | ({event.attempt_id} if event.attempt_id else set()),
            completed_attempt_ids=state.completed_attempt_ids | ({event.attempt_id} if event.complete and event.attempt_id else set()),
            completed_review_ids=state.completed_review_ids | ({event.review_id} if event.complete and event.review_id else set()),
        )
    if not event.complete:
        return next_state
    clean = event.assistance == "unassisted" and event.attempt_id not in state.tainted_attempt_ids
    return replace(
        next_state, count=min(3, state.count + 1) if clean else 0,
        completed_attempt_ids=state.completed_attempt_ids | {event.attempt_id},
        completed_review_ids=state.completed_review_ids | {event.review_id},
    )


@dataclass(frozen=True)
class _Trace:
    frames: tuple[str, ...]
    learner_turns: tuple[bool, ...]
    moves: tuple[str, ...]


def _trace(route: AuthoredRouteFacts) -> _Trace:
    if route.learner_color not in {"white", "black"}:
        raise ValueError("unsupported_learner_side")
    moves = tuple(route.moves)
    if not 0 < len(moves) <= 1000:
        raise ValueError("route_exceeds_bound")
    board = chess.Board(route.starting_fen)
    if not board.is_valid():
        raise ValueError("unsupported_position")
    frames, turns = [route.starting_fen], []
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("illegal_authored_move")
        turns.append(board.turn == (route.learner_color == "white"))
        board.push(move)
        frames.append(board.fen(en_passant="fen"))
    return _Trace(tuple(frames), tuple(turns), moves)


def _position(fen: str) -> str:
    if not chess.Board(fen).is_valid():
        raise ValueError("unsupported_position")
    return opening_position_key(fen)


def _ref(member: JoinMember, use: RouteUse) -> ComponentRef:
    return ComponentRef(member.presentation.presentation_id,
                        member.presentation.revision,
                        member.streak.evidence_epoch, use)


def plan_join(presentations: tuple[JoinMember, ...], route_context: RouteContext) -> JoinDecision:
    """Choose one unambiguous route-local pair, with no I/O or state mutation.

    RouteContext.complete certifies supplied authored conflict scope. Presentation
    and route read capabilities must be immutable for this call. All application
    races must be rechecked by #503 in its transaction; this is only a proposal.
    """
    context, route = route_context, route_context.route
    holds: set[JoinHold] = set()

    def hold(code, *ids):
        holds.add(JoinHold(code, tuple(sorted(set(ids)))))

    def decision(plan=None):
        return JoinDecision(plan, tuple(sorted(holds)))

    if context.variant != "standard":
        hold("unsupported_variant")
        return decision()
    if not context.complete or not context.available:
        hold("unavailable_route")
        return decision()
    if context.repertoire_revision < 1 or context.expected_study_revision < 1 or not route.source_revision or not route.route_id:
        hold("stale_revision")
        return decision()
    if not context.plan_id or not context.result_presentation_id:
        hold("missing_owner_identity")
        return decision()
    try:
        trace = _trace(route)
    except ValueError:
        hold("unsupported_position_or_route")
        return decision()

    # Public exact-position library reused; preserve full authored frames/history
    # separately. Other opponent branches are legal and remain separate uses.
    own_responses: dict[str, set[str]] = {}
    for authored in (route, *context.authored_routes):
        if authored.repertoire_id != route.repertoire_id:
            continue
        if authored.learner_color != route.learner_color:
            hold("learner_side_mismatch")
            return decision()
        if authored.source_revision != route.source_revision:
            hold("stale_revision")
            return decision()
        try:
            authored_trace = _trace(authored)
        except ValueError:
            hold("unsupported_position_or_route")
            return decision()
        for ply, uci in enumerate(authored_trace.moves):
            if authored_trace.learner_turns[ply]:
                own_responses.setdefault(_position(authored_trace.frames[ply]), set()).add(uci)
    conflicts = {key for key, moves in own_responses.items() if len(moves) > 1}

    entries: list[tuple[JoinMember, RouteUse]] = []
    by_ref: dict[ComponentRef, tuple[JoinMember, RouteUse]] = {}
    blocked: set[ComponentRef] = set()
    member_ids: set[str] = set()
    occurrence_ids: set[str] = set()
    for member in presentations:
        facts = member.presentation
        uses = tuple(use for use in member.route_uses if use.route_id == route.route_id)
        if not uses:
            continue
        if facts.presentation_id in member_ids:
            hold("ambiguous_component", facts.presentation_id)
            return decision()
        member_ids.add(facts.presentation_id)
        for use in uses:
            if use.occurrence_id in occurrence_ids:
                hold("ambiguous_occurrence", facts.presentation_id)
                return decision()
            occurrence_ids.add(use.occurrence_id)
            ref = _ref(member, use)
            by_ref[ref] = member, use
            entries.append((member, use))
            problems = []
            if (member.current_revision != facts.revision or facts.repertoire_revision != context.repertoire_revision or
                    (member.streak.presentation_id, member.streak.presentation_revision) !=
                    (facts.presentation_id, facts.revision)):
                problems.append("stale_revision")
            if member.active_attempt or member.pending_attempt:
                problems.append("active_attempt")
            if not member.streak.eligible or min(3, facts.clean_streak) != member.streak.count:
                problems.append("needs_clean_reviews")
            if not member.available:
                problems.append("unavailable_member")
            if not facts.introduced:
                problems.append("unintroduced_member")
            if (facts.repertoire_id != route.repertoire_id or facts.learner_color != route.learner_color
                    or facts.variant != context.variant or facts.route_id != route.route_id):
                problems.append("scope_mismatch")
            if use.stop_ply > len(trace.moves):
                problems.append("invalid_component_range")
            else:
                expected_moves = trace.moves[use.start_ply:use.stop_ply]
                if member.moves != expected_moves:
                    mismatches = [index for index, (actual, expected) in
                                  enumerate(zip(member.moves, expected_moves)) if actual != expected]
                    code = "route_moves_mismatch"
                    if mismatches and not trace.learner_turns[use.start_ply + mismatches[0]]:
                        code = "competing_opponent_fork"
                    problems.append(code)
                try:
                    if (_position(member.starting_fen) != _position(trace.frames[use.start_ply]) or
                            _position(member.ending_fen) != _position(trace.frames[use.stop_ply])):
                        problems.append("endpoint_mismatch")
                except ValueError:
                    problems.append("unsupported_position")
                own_plys = tuple(ply for ply in range(use.start_ply, use.stop_ply) if trace.learner_turns[ply])
                if not own_plys:
                    problems.append("no_learner_decision")
                else:
                    expected_components = tuple((_position(trace.frames[ply]), trace.moves[ply],
                                                 _position(trace.frames[ply + 1])) for ply in own_plys)
                    actual_components = tuple((item.position_key, item.response_uci, item.resulting_position_key)
                                              for item in facts.components)
                    if actual_components != expected_components:
                        problems.append("decision_sequence_mismatch")
                    expected_cues = tuple((_position(trace.frames[ply]), trace.moves[ply], _position(trace.frames[ply + 1]))
                                          for ply in range(own_plys[0], own_plys[-1]) if not trace.learner_turns[ply])
                    actual_cues = tuple((item.position_key, item.move_uci, item.resulting_position_key)
                                        for item in facts.opponent_cues)
                    if actual_cues != expected_cues:
                        problems.append("cue_sequence_mismatch")
                if any(_position(trace.frames[ply]) in conflicts for ply in range(use.start_ply, use.stop_ply)):
                    problems.append("conflicting_own_response")
            for problem in problems:
                hold(problem, facts.presentation_id)
                blocked.add(ref)

    entries.sort(key=lambda entry: (entry[1].start_ply, entry[1].stop_ply,
                                   entry[0].presentation.presentation_id, entry[1].occurrence_id))
    # Two overlapping authored components cannot be disambiguated by tie-breaking.
    for index, (left, left_use) in enumerate(entries):
        for right, right_use in entries[index + 1:]:
            if right_use.start_ply >= left_use.stop_ply:
                break
            hold("overlapping_components", left.presentation.presentation_id, right.presentation.presentation_id)
            blocked.update((_ref(left, left_use), _ref(right, right_use)))

    parents: dict[ComponentRef, set[ComponentRef]] = {}
    for connection in context.parent_connections:
        if connection.child.route_use.route_id == route.route_id:
            parents.setdefault(connection.child, set()).add(connection.parent)
    for child, (member, _) in by_ref.items():
        parent_id = member.presentation.recorded_parent_id
        if parent_id is None:
            continue
        matches = {ref for ref in by_ref if ref.presentation_id == parent_id}
        if len(matches) != 1:
            hold("ambiguous_parent" if matches else "unavailable_parent", child.presentation_id)
            blocked.add(child)
        else:
            parents.setdefault(child, set()).update(matches)
    # Refuse stale/ambiguous parent authority, rather than falling back to a
    # neighbour just because its endpoint happens to match.
    for child, recorded in parents.items():
        if child not in by_ref:
            for actual in by_ref:
                if (actual.presentation_id, actual.route_use) == (child.presentation_id, child.route_use):
                    hold("stale_parent_revision", actual.presentation_id)
                    blocked.add(actual)
            continue
        if len(recorded) != 1:
            hold("ambiguous_parent", child.presentation_id)
            blocked.add(child)
        elif next(iter(recorded)) not in by_ref:
            hold("unavailable_parent", child.presentation_id)
            blocked.add(child)

    candidates = []
    for (left, left_use), (right, right_use) in zip(entries, entries[1:]):
        left_ref, right_ref = _ref(left, left_use), _ref(right, right_use)
        if left_ref in blocked or right_ref in blocked:
            continue
        if left_ref.presentation_id == right_ref.presentation_id:
            hold("same_component", left_ref.presentation_id)
            continue
        if left_use.stop_ply > right_use.start_ply:
            continue
        gap = trace.moves[left_use.stop_ply:right_use.start_ply]
        if any(trace.learner_turns[left_use.stop_ply:right_use.start_ply]):
            hold("non_adjacent", left_ref.presentation_id, right_ref.presentation_id)
            continue
        recorded_pair = parents.get(right_ref) == {left_ref}
        if (right_ref in parents and not recorded_pair) or left_ref in parents:
            continue
        reason = "recorded_parent" if recorded_pair else "authored_adjacency"
        candidates.append((0 if recorded_pair else 1, left_use.start_ply,
                           left_ref.presentation_id, right_ref.presentation_id,
                           left, left_use, right, right_use, gap, reason))
    if not candidates:
        return decision()
    _, _, _, _, left, left_use, right, right_use, gap, reason = min(candidates, key=lambda item: item[:4])
    if context.result_presentation_id in member_ids:
        hold("invalid_result_identity")
        return decision()
    retained = tuple(sorted(set(
        RetainedUse(member.presentation.presentation_id, member.presentation.revision, use)
        for member, joined_use in ((left, left_use), (right, right_use))
        for use in member.route_uses if use != joined_use
    )))
    last_left = max(ply for ply in range(left_use.start_ply, left_use.stop_ply) if trace.learner_turns[ply])
    first_right = min(ply for ply in range(right_use.start_ply, right_use.stop_ply) if trace.learner_turns[ply])
    connecting = tuple(OpponentCue(position_key=_position(trace.frames[ply]), move_uci=trace.moves[ply],
                                  resulting_position_key=_position(trace.frames[ply + 1]))
                       for ply in range(last_left + 1, first_right))
    left_value, right_value = left.presentation, right.presentation
    result = Presentation(
        presentation_id=context.result_presentation_id, revision=1,
        repertoire_id=route.repertoire_id, repertoire_revision=context.repertoire_revision,
        variant=context.variant, learner_color=route.learner_color, route_id=route.route_id,
        components=left_value.components + right_value.components,
        opponent_cues=left_value.opponent_cues + connecting + right_value.opponent_cues,
        clean_streak=0, introduced=True,
        lineage=tuple(dict.fromkeys((*left_value.lineage, *right_value.lineage,
                                     left_value.presentation_id, right_value.presentation_id))),
        recorded_parent_id=None,
    )
    evidence = JoinEvidence(
        source_revision=route.source_revision, policy_id=route.policy_id,
        route_starting_fen=route.starting_fen, route_moves=trace.moves,
        components=(_ref(left, left_use), _ref(right, right_use)),
        starting_fen=left.starting_fen, ending_fen=right.ending_fen,
        moves=trace.moves[left_use.start_ply:right_use.stop_ply],
        intervening_opponent_moves=tuple(cue.move_uci for cue in connecting), retained_uses=retained, reason=reason,
    )
    plan = RouteJoinPlan(
        plan_id=context.plan_id, expected_study_revision=context.expected_study_revision,
        left=left_value, right=right_value, result=result, connecting_cues=connecting,
        unresolved_obligation_ids=context.unresolved_obligation_ids, evidence=evidence,
    )
    return decision(plan)
