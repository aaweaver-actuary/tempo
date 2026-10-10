"""Pure sequence eligibility; never unlock, admit, schedule, or rank a card."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import chess

from .cards import card_id

if TYPE_CHECKING:
    from .opening_graph import GraphStep


FrontierStatus = Literal[
    "structurally_unreachable", "prerequisite_unseen", "eligible", "already_introduced"
]
FRONTIER_POLICY_VERSION = "sequence-exposure-v1"


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
                route.publication, route.line_id, step.decision_index, step.parent_card_id,
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
