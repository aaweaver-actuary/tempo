"""Bounded injected-capability orchestration; emits values and never executes them."""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import islice

import chess
import chess.pgn
from tempo_evidence_math import opening_position_key

from app.contracts.product_v02.analysis import (
    DisplayedCardChange, EngineContext, EngineEvaluationPort, Proposal,
    ProposalDecision, RepertoireEdit,
)
from .acceptability import QualityPolicy, assess
from .ports import (
    CapabilityUnavailable, ExistingCardCandidate, ProposalBatch, ProposalLimits, ProposalResult,
    ProposalTarget, RetrievalFacts, SimilarityPort, SupportFacts,
)


def context_board(context: EngineContext) -> chess.Board:
    """Replay the actual supplied route, retaining full engine context unchanged."""
    EngineContext.model_validate_json(context.model_dump_json())
    if context.variant != "standard" or len(context.history_uci) > 1000:
        raise ValueError("unsupported_or_unbounded_history")
    board = chess.Board(context.starting_fen)
    if not board.is_valid():
        raise ValueError("invalid_starting_position")
    for move in context.history_uci:
        board.push_uci(move)
    target = chess.Board(context.target_fen)
    if not target.is_valid() or board.fen() != target.fen():
        raise ValueError("history_does_not_reach_target")
    return board


@dataclass(frozen=True)
class _Candidate:
    move: str
    exact: bool
    supports: tuple[SupportFacts, ...]

    def keys(self, excluding: str | None = None) -> set[str]:
        return {item.position_key for item in self.supports if item.position_key != excluding}


def _candidates(target: ProposalTarget, retrieved: RetrievalFacts, limits: ProposalLimits,
                board: chess.Board) -> tuple[_Candidate, ...]:
    grouped = {}
    for candidate in islice(retrieved.candidates, limits.max_candidates):
        move = chess.Move.from_uci(candidate.move_uci)
        if move not in board.legal_moves:
            continue
        if len(candidate.supporting_positions) > 256:
            raise ValueError("support_budget_exceeded")
        supports = tuple(item for item in candidate.supporting_positions
                         if item.selected_response_uci == candidate.move_uci)
        if any(len(item.occurrences) > 64 for item in supports):
            raise ValueError("provenance_budget_exceeded")
        if not supports:
            continue
        # Exact status requires exact-position evidence, not only a ranking flag.
        exact = candidate.exact_continuation and any(
            item.position_key == retrieved.target_position_key for item in supports)
        old = grouped.get(candidate.move_uci)
        combined = (old.supports if old else ()) + supports
        if len(combined) > 256:
            raise ValueError("support_budget_exceeded")
        grouped[candidate.move_uci] = _Candidate(candidate.move_uci,
            exact or bool(old and old.exact), combined)
    if target.previous_response_uci and target.previous_response_uci not in grouped:
        grouped[target.previous_response_uci] = _Candidate(target.previous_response_uci, True, ())
    if len(grouped) > limits.max_candidates:
        raise ValueError("candidate_budget_exceeded")
    return tuple(grouped.values())


def _retrieval_matches(target: ProposalTarget, retrieved: RetrievalFacts) -> bool:
    request, context = retrieved.request, target.context
    return ((retrieved.revision, retrieved.digest) == (target.source_revision, target.source_digest)
            and (request.repertoire_id, request.target_fen, request.learner_color, request.variant)
            == (target.repertoire_id, context.target_fen, context.learner_color, context.variant)
            and retrieved.target_position_key == opening_position_key(context.target_fen))


def _explanation(target: ProposalTarget, candidate: _Candidate, quality,
                 retrieved: RetrievalFacts) -> str:
    evidence = [f"{target.kind}; selected {candidate.move}; quality policy {quality.policy.version}; "
        f"best minus candidate = {quality.loss_cp} CP; tolerance {quality.policy.max_loss_cp} CP "
        "from learner perspective. Exact context, engine version, budget, depth and scores are attached.",
        f"Distinct supporting positions: {len(candidate.keys())}; exact continuation: {candidate.exact}."]
    if quality.policy.adjustment_reason:
        evidence.append(f"Explicit tolerance adjustment: {quality.policy.adjustment_reason}.")
    seen = set()
    for support in candidate.supports:
        if support.position_key in seen:
            continue
        seen.add(support.position_key)
        evidence.append(f"Source position {support.position_key}; matching features: "
            + "; ".join(support.comparison.similarities)
            + "; differing features: " + ("; ".join(support.comparison.differences) or "none reported"))
        for source in support.occurrences:
            evidence.append(f"Route {source.repertoire_id}/{source.route_id} ply {source.ply}; "
                f"source revision {source.source_revision}; root {source.starting_fen}; "
                f"history {' '.join(source.history_uci)}; full FEN {source.full_fen}.")
    evidence.append(f"Source complete: {retrieved.source_complete}; retrieval truncation: "
                    + (", ".join(retrieved.truncation) or "none") + ".")
    evidence.append("Similarity is advisory; the original selected response remains until displayed approval.")
    return "\n".join(evidence)


def _displayed_pgn(context: EngineContext, move: str) -> str:
    board = context_board(context)
    board.push_uci(move)
    return chess.pgn.Game.from_board(board).accept(
        chess.pgn.StringExporter(headers=False, variations=False, comments=False))


def _card_is_displayed(target: ProposalTarget, card: DisplayedCardChange, move: str) -> bool:
    if card.learner_color != target.context.learner_color:
        return False
    board = chess.Board(target.context.starting_fen)
    route = target.context.history_uci + (move,)
    for ply in range(len(route)):
        if (board.fen() == chess.Board(card.starting_fen).fen()
                and card.moves == route[ply:] and card.moves[-1] == move):
            break
        board.push_uci(route[ply])
    else:
        return False
    if card.action == "add":
        return card.presentation_id is None and card.expected_revision is None
    existing = target.existing_card
    return (card.action == "update" and existing is not None
            and (card.presentation_id, card.expected_revision)
            == (existing.presentation_id, existing.presentation_revision))


def _existing_matches(target: ProposalTarget, position_key: str) -> bool:
    card = target.existing_card
    return (card is not None and card.available
            and (card.repertoire_id, card.repertoire_revision, card.position_key, card.selected_response_uci)
            == (target.repertoire_id, target.source_revision, position_key, target.previous_response_uci))


class ProposalCoordinator:
    def __init__(self, similarity: SimilarityPort, engine: EngineEvaluationPort, *,
                 quality_policy: QualityPolicy = QualityPolicy(), limits: ProposalLimits = ProposalLimits()):
        self.similarity, self.engine = similarity, engine
        self.quality_policy, self.limits = quality_policy, limits

    def propose(self, target: ProposalTarget) -> ProposalResult:
        board = context_board(target.context)
        if (board.turn != (target.context.learner_color == "white")
                or target.source_revision < 1 or target.proposal_revision < 1
                or len(target.source_digest) != 64
                or len(target.displayed_card_changes) > self.limits.max_candidates):
            raise ValueError("invalid_proposal_target")
        if target.previous_response_uci and chess.Move.from_uci(target.previous_response_uci) not in board.legal_moves:
            raise ValueError("illegal_selected_response")
        if target.kind == "covered_outlier" and target.previous_response_uci is None:
            raise ValueError("outlier_requires_selected_response")
        try:
            retrieved = self.similarity.retrieve(repertoire_id=target.repertoire_id,
                target_fen=target.context.target_fen, learner_color=target.context.learner_color,
                variant=target.context.variant, max_candidates=self.limits.max_candidates)
        except CapabilityUnavailable:
            return ProposalResult("inconclusive", "similarity_unavailable")
        if not _retrieval_matches(target, retrieved):
            return ProposalResult("inconclusive", "stale_or_mismatched_retrieval")
        if (retrieved.status not in {"ready", "empty_neighbourhood"}
                or retrieved.exact_reason == "conflicting_own_responses"):
            return ProposalResult("inconclusive", retrieved.exact_reason if
                retrieved.exact_reason == "conflicting_own_responses" else retrieved.status,
                truncation=retrieved.truncation)
        if len(retrieved.candidates) > self.limits.max_candidates:
            return ProposalResult("inconclusive", "candidate_budget_exceeded", truncation=retrieved.truncation)
        try:
            candidates = _candidates(target, retrieved, self.limits, board)
        except ValueError as error:
            return ProposalResult("inconclusive", str(error), truncation=retrieved.truncation)
        if not candidates:
            return ProposalResult("inconclusive" if retrieved.truncation else "no_sound_candidate",
                                  "no_legal_familiar_candidate_in_scanned_scope", truncation=retrieved.truncation)
        # Every legal familiar candidate is explicit, irrespective of engine rank.
        requested = tuple(item.move for item in candidates)
        try:
            evidence = self.engine.evaluate(target.context, requested)
        except CapabilityUnavailable:
            return ProposalResult("inconclusive", "engine_unavailable", truncation=retrieved.truncation)
        if evidence.context != target.context or set(evidence.requested_candidates) != set(requested):
            return ProposalResult("inconclusive", "engine_request_mismatch", engine_evidence=(evidence,))
        quality = tuple(assess(target.context, evidence, item.move, self.quality_policy) for item in candidates)
        acceptable = {item.move_uci for item in quality if item.status == "acceptable"}
        eligible = [item for item in candidates if item.move in acceptable and item.supports]
        if target.kind == "covered_outlier":
            current_support = next((len(item.keys(retrieved.target_position_key)) for item in candidates
                                    if item.move == target.previous_response_uci), 0)
            eligible = [item for item in eligible if item.move != target.previous_response_uci
                and len(item.keys(retrieved.target_position_key)) >= self.limits.min_outlier_support
                and len(item.keys(retrieved.target_position_key)) > current_support]
        if not eligible:
            uncertain = any(item.status == "inconclusive" for item in quality) or bool(retrieved.truncation)
            return ProposalResult("inconclusive" if uncertain else "no_sound_candidate",
                "no_proved_sound_supported_candidate_in_scanned_scope", quality=quality,
                engine_evidence=(evidence,), truncation=retrieved.truncation)
        eligible.sort(key=lambda item: (not item.exact, -len(item.keys()),
                      -max(support.comparison.score for support in item.supports), item.move))
        selected = eligible[0]
        selected_quality = next(item for item in quality if item.move_uci == selected.move)
        explanation = _explanation(target, selected, selected_quality, retrieved)
        common = dict(selected_move_uci=selected.move, quality=quality,
                      engine_evidence=(evidence,), truncation=retrieved.truncation)
        if target.existing_card is not None and not _existing_matches(target, retrieved.target_position_key):
            return ProposalResult("inconclusive", "existing_card_identity_or_introduction_unproved", **common)
        if target.existing_card is not None and selected.move == target.previous_response_uci:
            if (target.existing_card.introduction == "unknown"
                    or (target.existing_card.introduction == "introduced"
                        and not target.existing_card.introduction_matches_current_content)):
                return ProposalResult("inconclusive", "existing_card_identity_or_introduction_unproved", **common)
            practice = ExistingCardCandidate(target.existing_card,
                "introduction_candidate" if target.existing_card.introduction == "unintroduced" else "practice_candidate",
                selected.move, target.source_revision, target.source_digest, explanation, (evidence,))
            return ProposalResult("existing_card", "existing_approved_response_preparation", existing_card=practice, **common)
        if selected.move == target.previous_response_uci:
            return ProposalResult("inconclusive", "existing_response_requires_actual_card_mapping", **common)
        cards = tuple(card for card in target.displayed_card_changes if _card_is_displayed(target, card, selected.move))
        if target.existing_card is not None and not any(card.action == "update" for card in cards):
            return ProposalResult("inconclusive", "exact_displayed_existing_card_edit_required", **common)
        if target.displayed_card_changes and not cards:
            return ProposalResult("inconclusive", "displayed_card_change_does_not_match_response", **common)
        if not cards:
            cards = (DisplayedCardChange(action="add", presentation_id=None, expected_revision=None,
                starting_fen=target.context.target_fen, moves=(selected.move,),
                learner_color=target.context.learner_color),)
        edit = RepertoireEdit(route_id=target.route_id, position_key=retrieved.target_position_key,
            previous_response_uci=target.previous_response_uci, proposed_response_uci=selected.move,
            displayed_pgn=_displayed_pgn(target.context, selected.move))
        proposal = Proposal(proposal_id=target.proposal_id, proposal_revision=target.proposal_revision,
            repertoire_id=target.repertoire_id, source_revision=target.source_revision,
            source_digest=target.source_digest, display_digest="0" * 64,
            title=f"Review {target.kind}: {selected.move}", explanation=explanation,
            edits=(edit,), card_changes=cards, engine_evidence=(evidence,),
            supporting_position_keys=tuple(sorted(selected.keys())), max_loss_cp=self.quality_policy.max_loss_cp,
            threshold_adjustment_reason=self.quality_policy.adjustment_reason)
        proposal = proposal.model_copy(update={"display_digest": proposal.content_digest()})
        return ProposalResult("proposal", "explicit_displayed_approval_required", proposal=proposal, **common)

    def scan_covered(self, targets: tuple[ProposalTarget, ...]) -> ProposalBatch:
        """Composition supplies bounded covered target/history facts from #483/#496.

        Duplicate exact targets are one observation even through repeated routes.
        No broad inventory, provider call, scheduling or content write occurs here.
        """
        if len(targets) > 10000:
            raise ValueError("target_inventory_exceeds_bound")
        seen, results = set(), []
        for target in targets:
            key = (target.repertoire_id, opening_position_key(target.context.target_fen))
            if key in seen:
                continue
            if len(results) == self.limits.max_targets:
                return ProposalBatch(tuple(results), len(results), True)
            seen.add(key)
            results.append(self.propose(replace(target, kind="covered_outlier")))
        return ProposalBatch(tuple(results), len(results), False)


def approval_requires_revalidation(proposal: Proposal, decision: ProposalDecision, *,
                                   current_source_revision: int, current_source_digest: str) -> str | None:
    """Read-only guard; only #492 may execute accept/edit/dismiss.

    Editing can never reuse the earlier evaluation/display approval. The owner
    must recheck legality, quality, source revision and one-own-response integrity.
    """
    if decision.action == "edit":
        return "edited_proposal_requires_revalidation"
    if (proposal.source_revision, proposal.source_digest) != (current_source_revision, current_source_digest):
        return "stale_source_requires_revalidation"
    if not decision.matches_preview(proposal):
        return "changed_display_requires_revalidation"
    return None
