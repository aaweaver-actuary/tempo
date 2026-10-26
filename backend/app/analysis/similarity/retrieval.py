"""Bounded supplied-index retrieval. No engine, storage, provider or mutation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import islice
from typing import Iterable, Protocol

import chess
from tempo_evidence_math import opening_position_key

from .features import Comparison, compare


class KeyFacts(Protocol):
    @property
    def repertoire_id(self) -> str: ...
    @property
    def variant(self) -> str: ...
    @property
    def position(self) -> str: ...


class OccurrenceFacts(Protocol):
    @property
    def route_id(self) -> str: ...
    @property
    def policy_id(self) -> str: ...
    @property
    def source_revision(self) -> str: ...
    @property
    def learner_color(self) -> str: ...
    @property
    def ply(self) -> int: ...
    @property
    def full_fen(self) -> str: ...
    @property
    def starting_fen(self) -> str: ...
    @property
    def history(self) -> tuple[str, ...]: ...
    @property
    def next_move(self) -> str | None: ...
    @property
    def learner_turn(self) -> bool: ...
    @property
    def route_complete(self) -> bool: ...


class NodeFacts(Protocol):
    @property
    def key(self) -> KeyFacts: ...
    @property
    def selected_response(self) -> str | None: ...
    @property
    def conflict(self) -> bool: ...
    @property
    def occurrences(self) -> tuple[OccurrenceFacts, ...]: ...


class NodeInventory(Protocol):
    def __len__(self) -> int: ...
    def values(self) -> Iterable[NodeFacts]: ...


class IndexFacts(Protocol):
    """Read-only structural subset of #483 PositionIndex; composition injects it.

    Nodes/occurrences use #483's deterministic exact-key/route/ply ordering.
    No schema, physical mapping, reentry or lifecycle authority is imported.
    """
    @property
    def snapshot_id(self) -> str: ...
    @property
    def source_revision(self) -> str: ...
    @property
    def revision(self) -> int: ...
    @property
    def digest(self) -> str: ...
    @property
    def scope_id(self) -> str: ...
    @property
    def available(self) -> bool: ...
    @property
    def complete(self) -> bool: ...
    @property
    def nodes(self) -> NodeInventory: ...

    def lookup(self, repertoire_id: str, fen: str, variant: str = "standard") -> NodeFacts | None: ...


@dataclass(frozen=True)
class RetrievalRequest:
    repertoire_id: str
    target_fen: str
    learner_color: str
    variant: str = "standard"
    max_nodes: int = 2048
    max_neighbours: int = 32
    max_candidates: int = 32
    max_occurrences: int = 32
    max_provenance: int = 8
    min_score: int = 800

    def __post_init__(self):
        if not self.repertoire_id or self.learner_color not in {"white", "black"}:
            raise ValueError("repertoire_id_and_learner_color_required")
        for name, minimum, maximum in (("max_nodes", 1, 10000), ("max_neighbours", 1, 256),
                ("max_candidates", 1, 256), ("max_occurrences", 1, 256),
                ("max_provenance", 1, 64), ("min_score", 0, 1000)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"invalid_{name}")


@dataclass(frozen=True, order=True)
class SourceOccurrence:
    repertoire_id: str
    route_id: str
    ply: int
    policy_id: str
    source_revision: str
    full_fen: str
    starting_fen: str
    history_uci: tuple[str, ...]


@dataclass(frozen=True)
class SupportingPosition:
    position_key: str
    full_fen: str
    selected_response_uci: str
    selected_response_san: str
    comparison: Comparison
    occurrences: tuple[SourceOccurrence, ...]
    provenance_truncated: bool


@dataclass(frozen=True)
class FamiliarCandidate:
    move_uci: str
    target_san: str
    exact_continuation: bool
    supporting_positions: tuple[SupportingPosition, ...]
    reason: str
    evaluation_status: str = "not_evaluated"

    @property
    def distinct_support(self) -> int:
        return len({item.position_key for item in self.supporting_positions})


@dataclass(frozen=True)
class RetrievalResult:
    request: RetrievalRequest
    snapshot_id: str
    source_revision: str
    revision: int
    digest: str
    scope_id: str
    source_complete: bool
    status: str
    target_position_key: str | None
    exact: SupportingPosition | None
    exact_reason: str
    neighbours: tuple[SupportingPosition, ...]
    candidates: tuple[FamiliarCandidate, ...]
    scanned_nodes: int
    eligible_neighbours: int
    legal_candidate_count: int
    truncation: tuple[str, ...]


def _source(node: NodeFacts, target: chess.Board, request: RetrievalRequest) -> SupportingPosition | None:
    if node.key.variant != request.variant or node.conflict or node.selected_response is None:
        return None
    occurrences = tuple(islice(node.occurrences, request.max_occurrences))
    refs = []
    representative = None
    comparison = None
    for occurrence in occurrences:
        if (occurrence.learner_color != request.learner_color or not occurrence.learner_turn
                or not occurrence.route_complete or occurrence.next_move != node.selected_response):
            continue
        source = chess.Board(occurrence.full_fen)
        if not source.is_valid() or opening_position_key(occurrence.full_fen) != node.key.position:
            continue
        move = chess.Move.from_uci(node.selected_response)
        if (move not in source.legal_moves or move not in target.legal_moves
                or source.piece_at(move.from_square) != target.piece_at(move.from_square)):
            continue
        current_comparison = compare(target, source, request.learner_color)
        if current_comparison is None or current_comparison.score < request.min_score:
            continue
        if representative is None:
            representative, comparison = source, current_comparison
        refs.append(SourceOccurrence(node.key.repertoire_id, occurrence.route_id, occurrence.ply,
            occurrence.policy_id, occurrence.source_revision, occurrence.full_fen,
            occurrence.starting_fen, tuple(occurrence.history)))
    if representative is None:
        return None
    return SupportingPosition(node.key.position, refs[0].full_fen, node.selected_response,
        representative.san(chess.Move.from_uci(node.selected_response)), comparison,
        tuple(refs[:request.max_provenance]),
        len(node.occurrences) > len(occurrences) or len(refs) > request.max_provenance)


def _merge_positions(first: SupportingPosition, second: SupportingPosition, limit: int) -> SupportingPosition:
    refs = sorted(set(first.occurrences + second.occurrences))
    return SupportingPosition(first.position_key, first.full_fen, first.selected_response_uci,
        first.selected_response_san, first.comparison, tuple(refs[:limit]),
        first.provenance_truncated or second.provenance_truncated or len(refs) > limit)


def retrieve(index: IndexFacts, request: RetrievalRequest) -> RetrievalResult:
    """Exact continuation first, then legal familiar candidates from distinct positions.

    Counts and order describe only the explicitly bounded scanned neighbourhood.
    Repeated route/import copies and cross-repertoire transpositions add provenance,
    never independent support. Legality is not engine acceptability.
    """
    def result(status, key=None, exact=None, exact_reason="not_checked", neighbours=(), candidates=(),
               scanned=0, eligible=0, legal_count=0, truncation=()):
        return RetrievalResult(request, index.snapshot_id, index.source_revision, index.revision,
            index.digest, index.scope_id, index.complete, status, key, exact, exact_reason,
            neighbours, candidates, scanned, eligible, legal_count, truncation)
    if request.variant != "standard":
        return result("unsupported_variant")
    target = chess.Board(request.target_fen)
    if not target.is_valid():
        raise ValueError("invalid_target_position")
    key = opening_position_key(request.target_fen)
    if target.turn != (request.learner_color == "white"):
        return result("not_learner_turn", key)
    if not index.available:
        return result("source_unavailable", key)
    exact_node = index.lookup(request.repertoire_id, request.target_fen, request.variant)
    exact = _source(exact_node, target, request) if exact_node is not None else None
    exact_reason = ("exact_continuation" if exact else "conflicting_own_responses"
                    if exact_node is not None and exact_node.conflict else "no_usable_exact_continuation")
    groups = {}
    scanned = 0
    provenance_truncated = bool(exact and exact.provenance_truncated)
    for node in islice(index.nodes.values(), request.max_nodes):
        scanned += 1
        provenance_truncated |= len(node.occurrences) > request.max_occurrences
        if node.key.position == key:  # Never disguise same identity as approximate support.
            continue
        support = _source(node, target, request)
        if support is None:
            continue
        identity = support.position_key, support.selected_response_uci
        groups[identity] = (_merge_positions(groups[identity], support, request.max_provenance)
                            if identity in groups else support)
    ordered = sorted(groups.values(), key=lambda item: (-item.comparison.score, item.position_key, item.selected_response_uci))
    neighbours = tuple(ordered[:request.max_neighbours])
    moves = {}
    for support in ((exact,) if exact else ()) + neighbours:
        moves.setdefault(support.selected_response_uci, []).append(support)
    candidates = []
    for move, supports in moves.items():
        is_exact = bool(exact and exact.selected_response_uci == move)
        count = len({item.position_key for item in supports})
        reason = f"{'Exact repertoire continuation; ' if is_exact else ''}selected at {count} distinct source position(s); legal in target; tactical quality not evaluated"
        candidates.append(FamiliarCandidate(move, target.san(chess.Move.from_uci(move)), is_exact, tuple(supports), reason))
    candidates.sort(key=lambda item: (not item.exact_continuation, -item.distinct_support,
        -max(support.comparison.score for support in item.supporting_positions), item.move_uci))
    truncation = []
    if len(index.nodes) > scanned: truncation.append("nodes")
    if len(ordered) > len(neighbours): truncation.append("neighbours")
    if len(candidates) > request.max_candidates: truncation.append("candidates")
    if provenance_truncated or any(item.provenance_truncated for item in ordered): truncation.append("provenance")
    return result("ready" if candidates else "empty_neighbourhood", key, exact, exact_reason,
        neighbours, tuple(candidates[:request.max_candidates]), scanned, len(ordered), len(candidates), tuple(truncation))


@dataclass(frozen=True)
class OutlierCandidate:
    position_key: str
    current_response_uci: str
    current_neighbour_support: int
    alternatives: tuple[FamiliarCandidate, ...]
    retrieval: RetrievalResult
    reason: str


@dataclass(frozen=True)
class OutlierScanResult:
    request: RetrievalRequest
    max_targets: int
    max_outliers: int
    min_support: int
    max_comparison_nodes: int
    snapshot_id: str
    source_revision: str
    revision: int
    digest: str
    scope_id: str
    source_complete: bool
    status: str
    outliers: tuple[OutlierCandidate, ...]
    scanned_nodes: int
    examined_positions: int
    comparison_nodes: int
    truncation: tuple[str, ...]


def scan_outliers(index: IndexFacts, request: RetrievalRequest, *, max_targets: int = 8,
                  max_outliers: int = 8, min_support: int = 2,
                  max_comparison_nodes: int = 16384) -> OutlierScanResult:
    """Advisory unusual covered responses; no majority-correct or edit decision.

    A legal alternative needs >= min_support distinct *other* positions and more
    support than the current move among those positions. Target/provenance and
    total neighbour-node budgets bound the scan even when no outlier is found.
    """
    for name, value, upper in (("max_targets", max_targets, 32), ("max_outliers", max_outliers, 32),
            ("min_support", min_support, 256), ("max_comparison_nodes", max_comparison_nodes, 50000)):
        if type(value) is not int or not 1 <= value <= upper:
            raise ValueError(f"invalid_{name}")
    def result(status, outliers=(), scanned=0, examined=0, comparisons=0, truncation=()):
        return OutlierScanResult(request, max_targets, max_outliers, min_support, max_comparison_nodes,
            index.snapshot_id, index.source_revision, index.revision,
            index.digest, index.scope_id, index.complete, status, outliers,
            scanned, examined, comparisons, truncation)
    if request.variant != "standard":
        return result("unsupported_variant")
    if not index.available:
        return result("source_unavailable")
    targets = []
    scanned = 0
    truncation = []
    for node in islice(index.nodes.values(), request.max_nodes):
        scanned += 1
        if (node.key.repertoire_id != request.repertoire_id or node.key.variant != request.variant
                or node.conflict or node.selected_response is None):
            continue
        if len(node.occurrences) > request.max_occurrences and "provenance" not in truncation:
            truncation.append("provenance")
        occurrence = next((item for item in islice(node.occurrences, request.max_occurrences)
            if item.learner_turn and item.learner_color == request.learner_color
            and item.route_complete and item.next_move == node.selected_response), None)
        if occurrence is not None:
            targets.append(occurrence.full_fen)
    if len(index.nodes) > scanned: truncation.append("target_nodes")
    if len(targets) > max_targets: truncation.append("targets")
    outliers = []
    comparisons = examined = 0
    for fen in targets[:max_targets]:
        if comparisons >= max_comparison_nodes:
            if "comparison_nodes" not in truncation:
                truncation.append("comparison_nodes")
            break
        remaining = max_comparison_nodes - comparisons
        target_request = replace(request, target_fen=fen, max_nodes=min(request.max_nodes, remaining))
        retrieved = retrieve(index, target_request)
        examined += 1
        comparisons += retrieved.scanned_nodes
        if retrieved.truncation and "neighbourhoods" not in truncation:
            truncation.append("neighbourhoods")
        if remaining < request.max_nodes and "nodes" in retrieved.truncation and "comparison_nodes" not in truncation:
            truncation.append("comparison_nodes")
        if retrieved.exact is None:
            continue
        current = retrieved.exact.selected_response_uci
        current_support = len({item.position_key for item in retrieved.neighbours
                               if item.selected_response_uci == current})
        alternatives = tuple(candidate for candidate in retrieved.candidates
            if candidate.move_uci != current and candidate.distinct_support >= min_support
            and candidate.distinct_support > current_support)
        if alternatives:
            outliers.append(OutlierCandidate(retrieved.target_position_key, current, current_support,
                alternatives, retrieved,
                "Other responses have more distinct comparable-position support; legality only, not evidence of correctness or an approved edit"))
    if len(outliers) > max_outliers: truncation.append("outliers")
    return result("ready" if outliers else "no_outliers", tuple(outliers[:max_outliers]),
        scanned, examined, comparisons, tuple(truncation))
