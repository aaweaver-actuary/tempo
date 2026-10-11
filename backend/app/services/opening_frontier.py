"""Pure sequence eligibility; never unlock, admit, schedule, or rank a card."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import chess

from .cards import card_id
from .opening_preparedness import ProbabilityEvidence
from .opening_segmentation import decision_identity, opening_position_key

if TYPE_CHECKING:
    from .opening_graph import GraphStep
    from .opening_policy_reach import LearnerPolicy


FrontierStatus = Literal[
    "structurally_unreachable", "prerequisite_unseen", "eligible", "already_introduced"
]
FRONTIER_POLICY_VERSION = "sequence-exposure-v1"
POLICY_FRONTIER_VERSION = "policy-sequence-frontier-v1"


@dataclass(frozen=True)
class OpeningFrontierPublication:
    repertoire_id: str
    generation: int
    scope_digest: str


@dataclass(frozen=True)
class OpeningFrontierRoute:
    publication: OpeningFrontierPublication
    line_id: str
    steps: tuple[GraphStep, ...]


@dataclass(frozen=True)
class OpeningFrontierCard:
    card_id: str
    repertoire_ids: tuple[str, ...]
    introduced_at: str | None = None
    state: str = "locked"  # Diagnostic metadata, never an eligibility predicate.
    archived: bool = False
    deleted: bool = False
    superseded: bool = False
    pending_validation: bool = False
    integrity_blocked_repertoire_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpeningFrontierReview:
    """One persisted review row; pending attempts/checkpoints are not reviews."""

    review_id: str
    card_id: str
    source_kind: str = "study"
    invalidated_at: str | None = None
    outcome: str = "again"
    guided: bool = False


@dataclass(frozen=True)
class OpeningFrontierObligation:
    """Caller-qualified game evidence, carried through without policy changes."""

    repertoire_id: str
    card_id: str
    kind: Literal["real_game_miss", "approved_gameplay_opportunity"]
    source_id: str


@dataclass(frozen=True)
class OpeningFrontierSnapshot:
    snapshot_id: str
    publications: tuple[OpeningFrontierPublication, ...]
    routes: tuple[OpeningFrontierRoute, ...]
    cards: tuple[OpeningFrontierCard, ...]
    reviews: tuple[OpeningFrontierReview, ...] = ()
    obligations: tuple[OpeningFrontierObligation, ...] = ()


@dataclass(frozen=True)
class OpeningRouteEligibility:
    publication: OpeningFrontierPublication
    line_id: str
    decision_index: int
    trained_color: str
    first_decision_index: int
    last_decision_index: int
    parent_card_id: str | None
    prerequisite_card_ids: tuple[str, ...]
    missing_prerequisite_card_ids: tuple[str, ...]
    exposure_review_ids: tuple[str, ...]
    blocked_reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.blocked_reasons and not self.missing_prerequisite_card_ids


@dataclass(frozen=True)
class OpeningCardEligibility:
    card_id: str
    status: FrontierStatus | None
    reasons: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    routes: tuple[OpeningRouteEligibility, ...]
    obligations: tuple[OpeningFrontierObligation, ...]

    @property
    def qualifying_routes(self) -> tuple[OpeningRouteEligibility, ...]:
        return tuple(route for route in self.routes if route.eligible)


@dataclass(frozen=True)
class OpeningFrontier:
    snapshot_id: str
    policy_version: str
    publications: tuple[OpeningFrontierPublication, ...]
    cards: tuple[OpeningCardEligibility, ...]

    @property
    def eligible_card_ids(self) -> tuple[str, ...]:
        """Stable serialization order, with no preference or score implied."""
        return tuple(card.card_id for card in self.cards if card.status == "eligible")


def _publication_key(publication: OpeningFrontierPublication) -> tuple:
    return publication.repertoire_id, publication.generation, publication.scope_digest


def _card_exclusions(card: OpeningFrontierCard) -> tuple[str, ...]:
    return tuple(sorted(reason for reason, excluded in (
        ("archived", card.archived), ("deleted", card.deleted),
        ("superseded", card.superseded), ("pending_validation", card.pending_validation),
    ) if excluded))


def _segment_geometry(step: GraphStep) -> tuple[str | None, tuple[str, ...]]:
    """Verify content and decision metadata without changing graph topology."""
    try:
        board = chess.Board(step.starting_fen)
        if not board.is_valid() or step.trained_color not in {"white", "black"}:
            return None, ("invalid_segment",)
        trained_color = chess.WHITE if step.trained_color == "white" else chess.BLACK
        decision_keys: list[str] = []
        last_moving_color = None
        for move_uci in step.moves:
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                return None, ("illegal_move",)
            last_moving_color = board.turn
            if board.turn == trained_color:
                decision_keys.append(" ".join(board.fen().split()[:4]))
            board.push(move)
        if not decision_keys or last_moving_color != trained_color:
            return None, ("invalid_segment",)
        reasons: set[str] = set()
        if card_id(step.starting_fen, step.moves) != step.card_id:
            reasons.add("card_identity_mismatch")
        if (step.segment_kind not in {"prefix", "decision"}
                or (step.segment_kind == "decision" and len(decision_keys) != 1)):
            reasons.add("invalid_segment_kind")
        if (step.first_decision_index < 0
                or step.last_decision_index - step.first_decision_index + 1 != len(decision_keys)
                or tuple(decision_keys) != step.decision_fen_keys
                or step.decision_fen_key != decision_keys[-1]):
            reasons.add("decision_metadata_mismatch")
        return " ".join(board.fen().split()[:4]), tuple(sorted(reasons))
    except (ValueError, TypeError, IndexError):
        return None, ("invalid_segment",)


def project_opening_frontier(snapshot: OpeningFrontierSnapshot) -> OpeningFrontier:
    """Classify one explicit snapshot using exposure along one complete route.

    Inputs must use immutable tuples and existing published GraphStep identities.
    A caller supplies coherent, persisted evidence; this function neither reads
    nor certifies a live database. Duplicate conflicting card/publication/review
    records are snapshot errors rather than an arbitrary last-record-wins policy.
    """
    return _project_opening_frontier(snapshot)


def _project_opening_frontier(snapshot: OpeningFrontierSnapshot, *,
                              step_blocks: dict[GraphStep, tuple[str, ...]] | None = None) -> OpeningFrontier:
    publications_by_repertoire: dict[str, OpeningFrontierPublication] = {}
    for publication in snapshot.publications:
        previous_publication = publications_by_repertoire.setdefault(publication.repertoire_id, publication)
        if previous_publication != publication:
            raise ValueError(f"Conflicting publications for repertoire {publication.repertoire_id}")
    cards_by_id: dict[str, OpeningFrontierCard] = {}
    for card in snapshot.cards:
        previous_card = cards_by_id.setdefault(card.card_id, card)
        if previous_card != card:
            raise ValueError(f"Conflicting card snapshots for {card.card_id}")
    reviews_by_id: dict[str, OpeningFrontierReview] = {}
    exposure_by_card: dict[str, set[str]] = defaultdict(set)
    for review in snapshot.reviews:
        if not review.review_id:
            raise ValueError("Saved review evidence requires a review identity")
        previous_review = reviews_by_id.setdefault(review.review_id, review)
        if previous_review != review:
            raise ValueError(f"Conflicting saved review evidence for {review.review_id}")
        if review.source_kind == "study" and review.invalidated_at is None:
            exposure_by_card[review.card_id].add(review.review_id)

    # Occurrence input order is not route identity. Equivalent copies collapse
    # before checking for genuinely conflicting representations of a line.
    unique_routes = {
        OpeningFrontierRoute(route.publication, route.line_id, tuple(sorted(
            route.steps, key=lambda step: (step.decision_index, repr(step)),
        ))) for route in snapshot.routes
    }
    route_copy_counts = Counter((_publication_key(route.publication), route.line_id) for route in unique_routes)
    route_results_by_card: dict[str, list[OpeningRouteEligibility]] = defaultdict(list)
    # Geometry is shared by identical occurrences; route dependencies never are.
    geometry_by_step: dict[GraphStep, tuple[str | None, tuple[str, ...]]] = {}
    for route in sorted(unique_routes, key=lambda item: (
        _publication_key(item.publication), item.line_id, repr(item.steps),
    )):
        repertoire_id = route.publication.repertoire_id
        prefix_reasons: set[str] = set()
        if publications_by_repertoire.get(repertoire_id) != route.publication:
            prefix_reasons.add("stale_or_unpublished_route")
        if route_copy_counts[(_publication_key(route.publication), route.line_id)] > 1:
            prefix_reasons.add("conflicting_route")
        occurrence_counts = Counter(step.decision_index for step in route.steps)
        prerequisites: list[str] = []
        previous_step = None
        previous_end_key = None
        for expected_index, step in enumerate(sorted(route.steps, key=lambda item: (item.decision_index, repr(item)))):
            if step.repertoire_id != repertoire_id or step.line_id != route.line_id:
                prefix_reasons.add("route_context_mismatch")
            if step.decision_index != expected_index:
                prefix_reasons.add("decision_index_gap")
            if occurrence_counts[step.decision_index] > 1:
                prefix_reasons.add("conflicting_decision_occurrence")
            if previous_step is None:
                if step.parent_card_id is not None or step.first_decision_index != 0 or step.segment_kind != "prefix":
                    prefix_reasons.add("missing_root")
            else:
                if step.segment_kind != "decision":
                    prefix_reasons.add("invalid_segment_kind")
                if step.parent_card_id != previous_step.card_id:
                    prefix_reasons.add("parent_link_mismatch")
                if step.first_decision_index != previous_step.last_decision_index + 1:
                    prefix_reasons.add("decision_range_gap")
                if step.trained_color != previous_step.trained_color:
                    prefix_reasons.add("trained_color_mismatch")
                try:
                    starting_key = " ".join(chess.Board(step.starting_fen).fen().split()[:4])
                    if previous_end_key is not None and starting_key != previous_end_key:
                        prefix_reasons.add("board_discontinuity")
                except (ValueError, TypeError):
                    prefix_reasons.add("invalid_segment")
            if step not in geometry_by_step:
                geometry_by_step[step] = _segment_geometry(step)
            end_key, geometry_reasons = geometry_by_step[step]
            prefix_reasons.update(geometry_reasons)
            if step_blocks:
                prefix_reasons.update(step_blocks.get(step, ()))
            card = cards_by_id.get(step.card_id)
            if card is None:
                prefix_reasons.add("missing_card")
            elif repertoire_id not in card.repertoire_ids:
                prefix_reasons.add("card_out_of_scope")
            elif _card_exclusions(card) or repertoire_id in card.integrity_blocked_repertoire_ids:
                prefix_reasons.add("unavailable_route_card")
            missing_prerequisites = tuple(dict.fromkeys(
                prerequisite for prerequisite in prerequisites if not exposure_by_card.get(prerequisite)
            ))
            exposure_ids = tuple(sorted({
                review_id for prerequisite in prerequisites for review_id in exposure_by_card.get(prerequisite, ())
            }))
            route_results_by_card[step.card_id].append(OpeningRouteEligibility(
                route.publication, route.line_id, step.decision_index, step.trained_color,
                step.first_decision_index, step.last_decision_index, step.parent_card_id,
                tuple(prerequisites), missing_prerequisites, exposure_ids, tuple(sorted(prefix_reasons)),
            ))
            prerequisites.append(step.card_id)
            previous_step, previous_end_key = step, end_key

    obligations_by_card: dict[str, list[OpeningFrontierObligation]] = defaultdict(list)
    for obligation in sorted(set(snapshot.obligations), key=lambda item: (
        item.card_id, item.repertoire_id, item.kind, item.source_id,
    )):
        card = cards_by_id.get(obligation.card_id)
        if card and obligation.repertoire_id in publications_by_repertoire and obligation.repertoire_id in card.repertoire_ids:
            obligations_by_card[obligation.card_id].append(obligation)

    results: list[OpeningCardEligibility] = []
    for identifier in sorted(set(cards_by_id) | set(route_results_by_card)):
        card = cards_by_id.get(identifier)
        routes = tuple(sorted(route_results_by_card.get(identifier, ()), key=lambda item: (
            _publication_key(item.publication), item.line_id, item.decision_index, repr(item),
        )))
        exclusions = set(_card_exclusions(card)) if card else {"missing_card"}
        if card:
            memberships = set(card.repertoire_ids) & publications_by_repertoire.keys()
            if not memberships:
                exclusions.add("outside_published_scope")
            elif memberships <= set(card.integrity_blocked_repertoire_ids):
                exclusions.add("integrity_blocked")
        status: FrontierStatus | None = None
        reasons: tuple[str, ...] = ()
        if not exclusions:
            if card.introduced_at is not None:
                status = "already_introduced"
            elif any(route.eligible for route in routes):
                status = "eligible"
            elif any(not route.blocked_reasons for route in routes):
                status, reasons = "prerequisite_unseen", ("prerequisite_unseen",)
            else:
                status = "structurally_unreachable"
                reasons = tuple(sorted({reason for route in routes for reason in route.blocked_reasons})) or ("no_current_route",)
        results.append(OpeningCardEligibility(
            identifier, status, reasons, tuple(sorted(exclusions)), routes,
            tuple(obligations_by_card.get(identifier, ())),
        ))
    return OpeningFrontier(
        snapshot.snapshot_id, FRONTIER_POLICY_VERSION,
        tuple(sorted(publications_by_repertoire.values(), key=_publication_key)), tuple(results),
    )


@dataclass(frozen=True)
class OpeningPolicyRouteBinding:
    """An existing publication/line occurrence explicitly selected by the caller."""

    publication: OpeningFrontierPublication
    line_id: str


def _binding_key(binding: OpeningPolicyRouteBinding) -> tuple:
    return _publication_key(binding.publication), binding.line_id


@dataclass(frozen=True)
class OpeningFrontierValuationInput:
    """Already conditioned decision values; this projection does not estimate them."""

    snapshot_id: str
    policy_id: str
    policy_version: int
    card_id: str
    decision_id: str
    route_bindings: tuple[OpeningPolicyRouteBinding, ...]
    reach: ProbabilityEvidence
    readiness: ProbabilityEvidence
    provenance: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "route_bindings", tuple(sorted(set(self.route_bindings), key=_binding_key)))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))
        if not isinstance(self.reach, ProbabilityEvidence) or not isinstance(self.readiness, ProbabilityEvidence):
            raise ValueError("Frontier valuation requires explicit probability evidence")


@dataclass(frozen=True)
class PolicyOpeningCardEligibility:
    eligibility: OpeningCardEligibility
    decisions: tuple[OpeningFrontierValuationInput, ...]
    prerequisites: tuple[OpeningFrontierValuationInput, ...]


@dataclass(frozen=True)
class PolicyOpeningFrontier:
    snapshot_id: str
    policy: LearnerPolicy
    publications: tuple[OpeningFrontierPublication, ...]
    route_bindings: tuple[OpeningPolicyRouteBinding, ...]
    cards: tuple[PolicyOpeningCardEligibility, ...]
    version: str = POLICY_FRONTIER_VERSION
    exposure_version: str = FRONTIER_POLICY_VERSION

    @property
    def eligible_card_ids(self) -> tuple[str, ...]:
        return tuple(card.eligibility.card_id for card in self.cards if card.eligibility.status == "eligible")


def _policy_step_decisions(step: GraphStep, policy: LearnerPolicy,
                           actions: dict[str, str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Bind existing segment decisions to actions, without propagating probability."""
    if step.trained_color != policy.trained_color:
        return (), ("policy_trained_color_mismatch",)
    _, geometry_reasons = _segment_geometry(step)
    if geometry_reasons:
        return (), ()  # The existing complete-route classifier owns structural diagnostics.
    board = chess.Board(step.starting_fen)
    decision_ids: list[str] = []
    reasons: set[str] = set()
    for move in step.moves:
        if board.turn == (policy.trained_color == "white"):
            selected_action = actions.get(opening_position_key(board.fen()))
            if selected_action is None:
                reasons.add("missing_policy_action")
            elif selected_action != move:
                reasons.add("policy_action_mismatch")
            decision_ids.append(decision_identity(policy.repertoire_id, board.fen(),
                policy.trained_color, move, policy.policy_version))
        board.push_uci(move)
    return tuple(sorted(set(decision_ids))), tuple(sorted(reasons))


def project_policy_opening_frontier(snapshot: OpeningFrontierSnapshot, *, policy: LearnerPolicy,
                                    route_bindings: tuple[OpeningPolicyRouteBinding, ...],
                                    valuation_inputs: tuple[OpeningFrontierValuationInput, ...] = ()) -> PolicyOpeningFrontier:
    """Select current policy routes before complete-route exposure aggregation.

    Supplied values are passed through, never scored, normalized or made into
    exposure. Sources must bind this snapshot, policy version, published route
    and actual decision. The caller certifies durable review/game provenance.
    """
    selected_bindings = tuple(sorted(set(route_bindings), key=_binding_key))
    selected_binding_set = set(selected_bindings)
    publications = tuple(publication for publication in snapshot.publications
                         if publication.repertoire_id == policy.repertoire_id)
    if not isinstance(snapshot.snapshot_id, str) or not snapshot.snapshot_id.strip():
        raise ValueError("Policy frontier requires a source snapshot identity")
    for binding in selected_bindings:
        if (binding.publication.repertoire_id != policy.repertoire_id
                or binding.publication not in publications or not binding.line_id):
            raise ValueError("Foreign or stale policy route binding")
    selected_routes = tuple(replace(route, steps=tuple(replace(step,
        moves=tuple(step.moves), decision_fen_keys=tuple(step.decision_fen_keys)) for step in route.steps))
        for route in snapshot.routes if OpeningPolicyRouteBinding(route.publication, route.line_id) in selected_binding_set)
    if selected_binding_set != {OpeningPolicyRouteBinding(route.publication, route.line_id) for route in selected_routes}:
        raise ValueError("Missing selected published route")
    routed_card_ids = {step.card_id for route in selected_routes for step in route.steps}
    selected_cards = tuple(replace(card, repertoire_ids=tuple(card.repertoire_ids),
        integrity_blocked_repertoire_ids=tuple(card.integrity_blocked_repertoire_ids))
        for card in snapshot.cards if policy.repertoire_id in card.repertoire_ids
        or card.card_id in routed_card_ids)
    selected_card_ids = {card.card_id for card in selected_cards}
    obligations = tuple(obligation for obligation in snapshot.obligations
                        if obligation.repertoire_id == policy.repertoire_id and obligation.card_id in selected_card_ids)
    if any(not isinstance(obligation.source_id, str) or not obligation.source_id.strip() for obligation in obligations):
        raise ValueError("Game obligation requires durable source provenance")
    selected_snapshot = OpeningFrontierSnapshot(snapshot.snapshot_id, publications, selected_routes,
        selected_cards, tuple(review for review in snapshot.reviews if review.card_id in selected_card_ids), obligations)

    decision_ids_by_card: dict[str, set[str]] = defaultdict(set)
    route_bindings_by_card_decision: dict[tuple[str, str], set[OpeningPolicyRouteBinding]] = defaultdict(set)
    policy_block_reasons_by_step = {}
    selected_actions_by_position = {action.position_key: action.move_uci for action in policy.actions}
    for route in selected_routes:
        binding = OpeningPolicyRouteBinding(route.publication, route.line_id)
        for step in route.steps:
            decisions, policy_block_reasons_by_step[step] = _policy_step_decisions(step, policy, selected_actions_by_position)
            decision_ids_by_card[step.card_id].update(decisions)
            for decision_id in decisions:
                route_bindings_by_card_decision[step.card_id, decision_id].add(binding)
    selected_frontier = _project_opening_frontier(selected_snapshot, step_blocks=policy_block_reasons_by_step)
    structurally_valid_bindings = {(card.card_id, OpeningPolicyRouteBinding(route.publication, route.line_id))
        for card in selected_frontier.cards for route in card.routes if not route.blocked_reasons}
    valuation_inputs_by_card_decision: dict[tuple[str, str], OpeningFrontierValuationInput] = {}
    for supplied in valuation_inputs:
        if (supplied.snapshot_id != snapshot.snapshot_id or type(supplied.policy_version) is not int
                or (supplied.policy_id, supplied.policy_version) != (policy.policy_id, policy.policy_version)):
            raise ValueError("Foreign or stale frontier valuation input")
        identity = supplied.card_id, supplied.decision_id
        if (identity not in route_bindings_by_card_decision or not supplied.route_bindings
                or not set(supplied.route_bindings) <= route_bindings_by_card_decision[identity]
                or any((supplied.card_id, binding) not in structurally_valid_bindings for binding in supplied.route_bindings)):
            raise ValueError("Valuation input does not bind a valid selected card decision/route")
        if not supplied.provenance or any(not isinstance(source, str) or not source.strip() for source in supplied.provenance):
            raise ValueError("Supplied frontier valuation requires source provenance")
        previous = valuation_inputs_by_card_decision.setdefault(identity, supplied)
        if previous != supplied:
            raise ValueError("Conflicting frontier valuation inputs")

    def valuation_inputs_for_card(card_identifier: str) -> tuple[OpeningFrontierValuationInput, ...]:
        return tuple(valuation_inputs_by_card_decision.get((card_identifier, decision_id)) or OpeningFrontierValuationInput(
            snapshot.snapshot_id, policy.policy_id, policy.policy_version, card_identifier, decision_id,
            tuple(route_bindings_by_card_decision[card_identifier, decision_id]), ProbabilityEvidence.unknown("missing_reach"),
            ProbabilityEvidence.unknown("missing_readiness"), ())
            for decision_id in sorted(decision_ids_by_card[card_identifier]))

    cards = tuple(PolicyOpeningCardEligibility(card, valuation_inputs_for_card(card.card_id),
        tuple(value for prerequisite in sorted({identifier for route in card.routes
            for identifier in route.prerequisite_card_ids}) for value in valuation_inputs_for_card(prerequisite)))
        for card in selected_frontier.cards)
    return PolicyOpeningFrontier(snapshot.snapshot_id, policy, selected_frontier.publications, selected_bindings, cards)
