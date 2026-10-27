"""Pure conflict audit/eligibility; optional producers and infrastructure absent."""
from __future__ import annotations

import hashlib
import io
import json

import chess
import chess.pgn

from app.contracts.product_v02.analysis import (
    Proposal, ProposalDecision, RepertoireChangeReceipt, RepertoireEdit,
)
from .ports import ChangeStore, ValidatedChange


from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire import build_index

from .ports import (AffectedOccurrence, ChangeError, ConflictReport,
                    EligibilityDecision, ResponseConflict)


def conflict_report(snapshot: RepertoireSnapshot) -> ConflictReport:
    """Use the public exact-position index, retaining invalid-source diagnostics."""
    index = build_index(snapshot)
    conflicts = tuple(ResponseConflict(node.key, node.own_responses, tuple(
        AffectedOccurrence(item.key.repertoire_id, item.route_id, item.ply,
            item.next_move, tuple(sorted({binding.presentation_id for binding in item.bindings})))
        for item in node.occurrences if item.learner_turn and item.next_move
    )) for node in index.nodes.values() if node.conflict)
    return ConflictReport(index.snapshot_id, index.source_revision, index.revision,
        index.digest, conflicts, index.diagnostics, index.available, snapshot.membership.complete)


def study_eligibility(snapshot: RepertoireSnapshot, *, repertoire_id: str,
                      route_id: str, ply: int, source_revision: str,
                      revision: int, digest: str) -> EligibilityDecision:
    """Read-only owner decision. Study alone suspends attempts, without Again.

    Every use supplies the current source fence; an unpublished/old result cannot
    clear a block. Occurrence scope never globally blocks a shared physical card.
    """
    membership = snapshot.membership
    if (source_revision, revision, digest) != (membership.source_revision, membership.revision, membership.digest):
        return EligibilityDecision(False, "stale_integrity", True)
    if not membership.available or not membership.complete:
        return EligibilityDecision(False, "unpublished_integrity", True)
    index = build_index(snapshot)
    occurrences = index.routes.get((repertoire_id, route_id), ())
    if not 0 <= ply < len(occurrences):
        return EligibilityDecision(False, "unknown_occurrence", True)
    occurrence = occurrences[ply]
    if not occurrence.learner_turn or not occurrence.next_move:
        return EligibilityDecision(False, "not_own_decision", True)
    # A malformed source route cannot gain eligibility from its retained prefix.
    errors = [item for item in index.diagnostics
              if item.repertoire_id == repertoire_id and item.route_id == route_id
              and item.code != "own_response_conflict"]
    if errors:
        return EligibilityDecision(False, "invalid_source", True)
    if index.nodes[occurrence.key].conflict:
        return EligibilityDecision(False, "own_response_conflict", True)
    return EligibilityDecision(True, None, False)


def require_valid_change(snapshot: RepertoireSnapshot) -> None:
    report = conflict_report(snapshot)
    if not report.clean:
        raise ChangeError("conflict_pending_resolution" if report.conflicts else "invalid_source")


def _pgn(starting_fen: str, moves: tuple[str, ...]) -> str:
    game = chess.pgn.Game()
    game.setup(chess.Board(starting_fen))
    node = game
    for move in moves:
        node = node.add_variation(chess.Move.from_uci(move))
    return game.accept(chess.pgn.StringExporter(headers=False, variations=False, comments=False))


def _displayed_moves(starting_fen: str, pgn: str) -> tuple[str, ...]:
    # A display is one exact legal route. Reject parser recovery/extra games and
    # variations rather than allowing hidden accepted alternatives.
    if len(pgn.encode()) > 256000:
        raise ChangeError("display_exceeds_bound")
    wrapped = f'[SetUp "1"]\n[FEN "{starting_fen}"]\n\n{pgn}' if "[FEN " not in pgn else pgn
    stream = io.StringIO(wrapped)
    game = chess.pgn.read_game(stream)
    if (game is None or game.errors or chess.pgn.read_game(stream) is not None
            or game.headers.get("Variant", "Standard").lower() not in {"standard", "from position"}
            or game.board().fen() != chess.Board(starting_fen).fen()):
        raise ChangeError("invalid_displayed_pgn")
    node = game
    while node.variations:
        if len(node.variations) != 1:
            raise ChangeError("display_contains_alternatives")
        node = node.variations[0]
    return tuple(move.uci() for move in game.mainline_moves())


def _engine_allows(proposal: Proposal, occurrence, edit: RepertoireEdit) -> None:
    if not proposal.engine_evidence:
        # Engine-free structural choices must select a response already present
        # at a diagnosed conflict. No quality claim is manufactured for a repair.
        return
    evidence = [item for item in proposal.engine_evidence
        if item.context.variant == occurrence.key.variant
        and item.context.learner_color == occurrence.learner_color
        and item.context.starting_fen == occurrence.starting_fen
        and item.context.history_uci == occurrence.history
        and item.context.target_fen == occurrence.full_fen]
    if not evidence:
        raise ChangeError("engine_context_mismatch")
    accepted = False
    for item in evidence:
        candidates = [value for value in item.candidates if value.move_uci == edit.proposed_response_uci]
        best = item.best
        candidate = candidates[0] if len(candidates) == 1 else None
        if (item.unavailable_reason is not None or best is None or candidate is None
                or best.score is None or candidate.score is None
                or best.score.kind != "centipawns" or candidate.score.kind != "centipawns"
                or best.depth != candidate.depth or best.depth is None):
            continue
        board = chess.Board(occurrence.full_fen)
        if (chess.Move.from_uci(best.move_uci) not in board.legal_moves
                or chess.Move.from_uci(candidate.move_uci) not in board.legal_moves):
            continue
        loss = best.score.value - candidate.score.value
        if 0 <= loss <= proposal.max_loss_cp:
            accepted = True
    if not accepted:
        raise ChangeError("engine_acceptance_unproved")


def validate_proposal(snapshot: RepertoireSnapshot, proposal: Proposal) -> ValidatedChange:
    """Bind source, every route delta, cards and explicit supplied engine scores."""
    # Detach/revalidate at this public edge, including callers using model_copy.
    proposal = Proposal.model_validate_json(proposal.model_dump_json())
    snapshot = RepertoireSnapshot.model_validate_json(snapshot.model_dump_json())
    source = snapshot.membership
    if (proposal.source_revision, proposal.source_digest) != (source.revision, source.digest):
        raise ChangeError("stale_source")
    if proposal.display_digest != proposal.content_digest():
        raise ChangeError("display_digest_mismatch")
    if proposal.repertoire_id not in source.scope_repertoire_ids or snapshot.variant != "standard":
        raise ChangeError("repertoire_scope_mismatch")
    if not source.available or not source.complete:
        raise ChangeError("unpublished_source")
    if not proposal.edits and not proposal.card_changes:
        raise ChangeError("empty_change")
    if len(proposal.edits) > 1000 or len(proposal.card_changes) > 1000:
        raise ChangeError("change_exceeds_bound")
    index = build_index(snapshot)
    if any(item.code != "own_response_conflict" for item in index.diagnostics):
        raise ChangeError("invalid_source")
    routes = {item.route_id: item for item in source.routes}
    updates = {}
    for edit in proposal.edits:
        route = routes.get(edit.route_id)
        if route is None or route.repertoire_id != proposal.repertoire_id or edit.route_id in updates:
            raise ChangeError("ambiguous_route_edit")
        occurrences = [item for item in index.routes[proposal.repertoire_id, edit.route_id]
                       if item.key.position == edit.position_key and item.learner_turn
                       and item.next_move == edit.previous_response_uci]
        if len(occurrences) != 1:
            raise ChangeError("stale_or_ambiguous_occurrence")
        occurrence = occurrences[0]
        board = chess.Board(occurrence.full_fen)
        if chess.Move.from_uci(edit.proposed_response_uci) not in board.legal_moves:
            raise ChangeError("illegal_response")
        if not proposal.engine_evidence:
            node = index.nodes[occurrence.key]
            if not node.conflict or edit.proposed_response_uci not in node.own_responses:
                raise ChangeError("engine_evidence_required")
        _engine_allows(proposal, occurrence, edit)
        expected = (tuple(route.moves) if edit.proposed_response_uci == edit.previous_response_uci
                    else occurrence.history + (edit.proposed_response_uci,))
        displayed = _displayed_moves(route.starting_fen, edit.displayed_pgn)
        if displayed != expected:
            raise ChangeError("displayed_route_mismatch")
        updates[edit.route_id] = expected
    payload = snapshot.model_dump(mode="json")
    next_revision = source.revision + 1
    for route in payload["membership"]["routes"]:
        if route["route_id"] in updates:
            route["moves"] = list(updates[route["route_id"]])
            # Existing reentry grants cannot outlive changed authored content.
            route["reentries"] = []
    for boundary in payload["boundaries"]:
        if boundary["route_id"] in updates:
            boundary["terminal_ply"] = len(updates[boundary["route_id"]])
            boundary["deliberate"] = True
    payload["membership"]["revision"] = next_revision
    payload["membership"]["source_revision"] = f"approved:{proposal.display_digest}"
    payload["membership"]["snapshot_id"] = f"approved:{proposal.display_digest}"
    changed_positions = {item.position_key for item in proposal.edits}
    for mapping in payload["decision_presentations"]:
        mapping["repertoire_revision"] = next_revision
        if mapping["repertoire_id"] == proposal.repertoire_id and mapping["position_key"] in changed_positions:
            mapping["available"] = False
            mapping["introduction_matches_current_content"] = False
    # The existing public snapshot does not prove full-card executable identity.
    # Publication owner additionally validates all existing-card revisions and
    # retains their history; this policy validates legal displayed route scope.
    after_routes = payload["membership"]["routes"]
    for card in proposal.card_changes:
        if card.action == "add" and (card.presentation_id is not None or card.expected_revision is not None):
            raise ChangeError("new_card_identity_must_be_owner_assigned")
        if card.action != "add" and not any(
            item.repertoire_id == proposal.repertoire_id and item.presentation_id == card.presentation_id
            and item.presentation_revision == card.expected_revision
            for item in snapshot.decision_presentations):
            raise ChangeError("stale_card_mapping")
        matched = False
        for route in after_routes:
            if route["repertoire_id"] != proposal.repertoire_id or route["learner_color"] != card.learner_color:
                continue
            board = chess.Board(route["starting_fen"])
            for ply in range(len(route["moves"])):
                if (board.fen() == chess.Board(card.starting_fen).fen()
                        and tuple(route["moves"][ply:ply + len(card.moves)]) == card.moves):
                    matched = True
                board.push_uci(route["moves"][ply])
        if not matched:
            raise ChangeError("card_outside_displayed_routes")
    # Store a deterministic product snapshot digest; keep original in the plan.
    payload["membership"]["digest"] = hashlib.sha256(json.dumps(payload,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    after = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    final_report = conflict_report(after)
    original_conflicts = {(item.position, item.responses_uci) for item in conflict_report(snapshot).conflicts}
    if (any(item.code != "own_response_conflict" for item in final_report.diagnostics)
            or any((item.position, item.responses_uci) not in original_conflicts
                   or (item.position.repertoire_id == proposal.repertoire_id
                       and item.position.position in changed_positions)
                   for item in final_report.conflicts)):
        raise ChangeError("conflict_pending_resolution")
    return ValidatedChange(proposal, snapshot, after)


def manual_resolution(snapshot: RepertoireSnapshot, *, repertoire_id: str,
                      position: str, selected_uci: str, proposal_id: str) -> Proposal:
    """Preview winner + all losing-route replacements, retaining original source.

    Replacement ends after the selected response. Never graft an old losing
    continuation onto another move. The full before snapshot is retained by the
    approval store and the displayed PGN includes every removed tail.
    """
    index = build_index(snapshot)
    nodes = [item for item in index.nodes.values() if item.key.repertoire_id == repertoire_id
             and item.key.position == position and item.conflict]
    if len(nodes) != 1 or selected_uci not in nodes[0].own_responses:
        raise ChangeError("unknown_conflict_or_winner")
    edits = []
    seen = set()
    for occurrence in nodes[0].occurrences:
        if not occurrence.learner_turn or occurrence.next_move == selected_uci:
            continue
        if occurrence.route_id in seen:
            raise ChangeError("repeated_position_requires_explicit_edit")
        seen.add(occurrence.route_id)
        edits.append(RepertoireEdit(route_id=occurrence.route_id, position_key=position,
            previous_response_uci=occurrence.next_move, proposed_response_uci=selected_uci,
            displayed_pgn=_pgn(occurrence.starting_fen, occurrence.history + (selected_uci,))))
    route_sources = {item.route_id: item for item in snapshot.membership.routes}
    removed_tails = "\n".join(f"{item.route_id}: before {_pgn(route_sources[item.route_id].starting_fen, tuple(route_sources[item.route_id].moves))}; after {item.displayed_pgn}" for item in edits)
    proposal = Proposal(proposal_id=proposal_id, proposal_revision=1, repertoire_id=repertoire_id,
        source_revision=snapshot.membership.revision, source_digest=snapshot.membership.digest,
        display_digest="0"*64, title=f"Select {selected_uci}",
        explanation="Replace every competing own response with the selected move; the displayed routes end there. Original alternatives and tails remain retained evidence. Other routes and opponent branches are unchanged.\n" + removed_tails,
        edits=tuple(edits), card_changes=(), engine_evidence=(), supporting_position_keys=(position,))
    proposal = proposal.model_copy(update={"display_digest": proposal.content_digest()})
    validate_proposal(snapshot, proposal)
    return proposal


def _receipt_id(proposal: Proposal, status: str) -> str:
    key = json.dumps([proposal.proposal_id, proposal.proposal_revision, status], separators=(",", ":"))
    return "repertoire:" + hashlib.sha256(key.encode()).hexdigest()


class RepertoireChanges:
    """Implements shared RepertoireChangePort, with no infrastructure ownership.

    Store locks source + proposal/receipt in one borrowed transaction. Only the
    injected content/publication owner applies an approved plan or card changes.
    """
    def __init__(self, store: ChangeStore):
        self.store = store

    def preview_change(self, proposal: Proposal) -> RepertoireChangeReceipt:
        proposal = Proposal.model_validate_json(proposal.model_dump_json())
        with self.store.transaction(proposal.repertoire_id) as tx:
            existing = tx.receipt(proposal.proposal_id)
            if existing is not None:
                raise ChangeError("proposal_already_decided")
            current = tx.preview(proposal.proposal_id)
            if current is not None and current != proposal:
                raise ChangeError("edit_requires_decision")
            validate_proposal(tx.snapshot(), proposal)
            tx.save_preview(proposal)
            return RepertoireChangeReceipt(owner_receipt_id=_receipt_id(proposal, "preview"),
                proposal_id=proposal.proposal_id, status="preview", source_revision=proposal.source_revision,
                proposal=proposal, reason=None)

    preview = preview_change

    def decide(self, decision: ProposalDecision) -> RepertoireChangeReceipt:
        decision = ProposalDecision.model_validate_json(decision.model_dump_json())
        # Route lookup belongs to storage; public decision has no repertoire ID.
        # A store is scoped to a repertoire at construction / HTTP composition.
        with self.store.transaction("") as tx:
            current = tx.preview(decision.proposal_id)
            if current is None or not decision.matches_preview(current):
                raise ChangeError("stale_or_unapproved_preview")
            receipt = tx.receipt(decision.proposal_id)
            if receipt is not None:
                if decision.action == "accept" and receipt.status == "accepted":
                    return receipt
                if decision.action == "dismiss" and receipt.status == "dismissed":
                    return receipt
                raise ChangeError("proposal_already_decided")
            source = tx.snapshot()
            if decision.action == "edit":
                edited = decision.edited_proposal
                if (edited.proposal_id != current.proposal_id or edited.repertoire_id != current.repertoire_id
                        or edited.proposal_revision != current.proposal_revision + 1):
                    raise ChangeError("edited_proposal_identity_mismatch")
                validate_proposal(source, edited)
                tx.save_preview(edited)
                return RepertoireChangeReceipt(owner_receipt_id=_receipt_id(edited, "preview"),
                    proposal_id=edited.proposal_id, status="preview", source_revision=edited.source_revision,
                    proposal=edited, reason=None)
            if decision.action == "accept":
                change = validate_proposal(source, current)
                tx.publish(change)
                status, revision = "accepted", change.after.membership.revision
            else:
                status, revision = "dismissed", source.membership.revision
            receipt = RepertoireChangeReceipt(owner_receipt_id=_receipt_id(current, status),
                proposal_id=current.proposal_id, status=status, source_revision=revision, proposal=current, reason=None)
            tx.save_receipt(receipt)
            return receipt

    def accept_change(self, decision: ProposalDecision) -> RepertoireChangeReceipt:
        if decision.action != "accept": raise ChangeError("accept_action_required")
        return self.decide(decision)

    def edit_proposal(self, decision: ProposalDecision) -> RepertoireChangeReceipt:
        if decision.action != "edit": raise ChangeError("edit_action_required")
        return self.decide(decision)

    def dismiss_proposal(self, decision: ProposalDecision) -> RepertoireChangeReceipt:
        if decision.action != "dismiss": raise ChangeError("dismiss_action_required")
        return self.decide(decision)


def validate_source_rows(repertoire_id: str, rows: tuple[dict, ...]) -> None:
    """Guard old import/edit commands using the same public exact index.

    Caller owns current source read + repertoire row lock and supplies the
    proposed final source set. The guard does no I/O and creates no card mapping.
    """
    if len(rows) > 10000: raise ChangeError("source_exceeds_bound")
    routes = []
    colors = {row.get("trained_color") for row in rows}
    if len(colors) > 1: raise ChangeError("invalid_source")
    for row in rows:
        try:
            moves = json.loads(row["moves_json"])
            if not isinstance(moves, list) or row["trained_color"] not in {"white", "black"}:
                raise ValueError("invalid source")
            routes.append(dict(route_id=row["source_id"], repertoire_id=repertoire_id,
                policy_id=repertoire_id, learner_color=row["trained_color"],
                starting_fen=row["start_fen"], moves=moves, reentries=[]))
        except (ValueError, KeyError, TypeError) as error:
            raise ChangeError("invalid_source") from error
    try:
        value = RepertoireSnapshot.model_validate_json(json.dumps(dict(
            membership=dict(workspace_id="current-owner", snapshot_id="mutation-guard", revision=1,
                digest="0"*64, source_revision="locked-current-source", captured_at="2026-10-26T00:00:00Z",
                scope_id=repertoire_id, scope_repertoire_ids=[repertoire_id], complete=True, available=True,
                routes=routes), variant="standard", learner_color="white",
            boundaries=[dict(route_id=item["route_id"], terminal_ply=len(item["moves"]), deliberate=True)
                        for item in routes], decision_presentations=[])))
    except ValueError as error:
        raise ChangeError("invalid_source") from error
    require_valid_change(value)
