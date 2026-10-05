"""Pure comparisons of source-line depths using the production opening graph."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
import json
from typing import Iterator

import chess

from .cards import card_id
from .opening_graph import GraphInput, GraphStep, build_graph
from .opening_segmentation import POLICY_VERSION, POSITION_VERSION, decision_identity, stable_key

EVALUATION_VERSION = 1
MAX_SOURCE_LINES = 2_000
MAX_SOURCE_PLIES = 80_000
MAX_PREFIX_EVALUATION_LINE_PLIES = 512
MAX_GRAPH_STEPS = 40_000
ESTIMATE_BASIS = 'Structural counts only; not evidence of better learning or measured time savings.'


class PrefixEvaluationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceLine:
    id: str
    name: str
    start_fen: str
    moves: tuple[str, ...]
    trained_color: str
    saved_depth: int | None

    def __post_init__(self):
        object.__setattr__(self, 'moves', tuple(self.moves))

    def graph_line(self) -> dict:
        return {'id': self.id, 'start_fen': self.start_fen, 'moves_json': json.dumps(self.moves),
                'trained_color': self.trained_color, 'learner_decision_count': self.saved_depth}


@dataclass(frozen=True)
class SplitOverride:
    source_card_id: str
    source_revision: int
    shortened_card_id: str
    shortened_start_fen: str
    shortened_moves: tuple[str, ...]
    shortened_revision: int
    continuation_card_id: str
    continuation_start_fen: str
    continuation_moves: tuple[str, ...]
    continuation_revision: int

    def __post_init__(self):
        object.__setattr__(self, 'shortened_moves', tuple(self.shortened_moves))
        object.__setattr__(self, 'continuation_moves', tuple(self.continuation_moves))

    def graph_override(self) -> dict:
        return {'source_card_id': self.source_card_id, 'shortened_card_id': self.shortened_card_id,
                'shortened_start_fen': self.shortened_start_fen,
                'shortened_moves_json': json.dumps(self.shortened_moves),
                'continuation_card_id': self.continuation_card_id,
                'continuation_start_fen': self.continuation_start_fen,
                'continuation_moves_json': json.dumps(self.continuation_moves)}


@dataclass(frozen=True)
class PublishedCard:
    id: str
    start_fen: str
    moves: tuple[str, ...]
    trained_color: str
    revision: int
    archived: int
    linked: bool

    def __post_init__(self):
        object.__setattr__(self, 'moves', tuple(self.moves))


@dataclass(frozen=True)
class EvaluationSnapshot:
    repertoire_id: str
    graph_generation: int
    lines: tuple[SourceLine, ...]
    prefix_overrides: tuple[SplitOverride, ...]
    published_steps: tuple[GraphStep, ...]
    presentations: tuple[PublishedCard, ...] = ()

    def __post_init__(self):
        for field in ('lines', 'prefix_overrides', 'published_steps', 'presentations'):
            object.__setattr__(self, field, tuple(getattr(self, field)))


def ordered_steps(steps) -> tuple[GraphStep, ...]:
    return tuple(sorted(steps, key=lambda step: (step.line_id, step.decision_index)))


def snapshot_identity(snapshot: EvaluationSnapshot) -> str:
    return stable_key('structural-prefix-snapshot', EVALUATION_VERSION, POLICY_VERSION, POSITION_VERSION,
                      snapshot.repertoire_id, snapshot.graph_generation,
                      [asdict(line) for line in sorted(snapshot.lines, key=lambda line: line.id)],
                      [asdict(override) for override in sorted(snapshot.prefix_overrides, key=lambda override: override.source_card_id)],
                      [asdict(step) for step in ordered_steps(snapshot.published_steps)],
                      [asdict(card) for card in sorted(snapshot.presentations, key=lambda card: card.id)])


def validate_source(snapshot: EvaluationSnapshot) -> None:
    if len(snapshot.lines) > MAX_SOURCE_LINES:
        raise PrefixEvaluationError('limit_exceeded', 'Repertoire exceeds the bounded source evaluation limits.')
    for line in snapshot.lines:
        line_ply_count = len(line.moves)
        if line_ply_count > MAX_PREFIX_EVALUATION_LINE_PLIES:
            raise PrefixEvaluationError('limit_exceeded',
                f'Line {line.id} contains {line_ply_count} plies; prefix evaluation supports at most '
                f'{MAX_PREFIX_EVALUATION_LINE_PLIES} plies per source line. '
                'Use a repertoire with shorter source lines for this diagnostic.')
    if sum(len(line.moves) for line in snapshot.lines) > MAX_SOURCE_PLIES:
        raise PrefixEvaluationError('limit_exceeded', 'Repertoire exceeds the bounded source evaluation limits.')
    if len(snapshot.published_steps) > MAX_GRAPH_STEPS:
        raise PrefixEvaluationError('limit_exceeded', 'Repertoire exceeds the bounded graph evaluation limit.')
    if len({line.id for line in snapshot.lines}) != len(snapshot.lines):
        raise PrefixEvaluationError('unsupported_source', 'Source line identities are not unique.')
    for line in snapshot.lines:
        if line.trained_color not in {'white', 'black'} or not all(isinstance(move, str) for move in line.moves):
            raise PrefixEvaluationError('unsupported_source', f'Line {line.id} has invalid color or move data.')
        if type(line.saved_depth) is not int or line.saved_depth <= 0:
            raise PrefixEvaluationError('unsupported_source', f'Line {line.id} needs a positive saved training depth. Repair the source before evaluating.')


def _validate_override(override: SplitOverride) -> None:
    # Validate saved data, without replacing the production split/segmentation rules.
    if not override.shortened_moves or not override.continuation_moves:
        raise ValueError('Saved split has an empty child')
    if card_id(override.shortened_start_fen, override.shortened_moves) != override.shortened_card_id:
        raise ValueError('Saved split shortened identity differs from its presentation')
    if card_id(override.continuation_start_fen, override.continuation_moves) != override.continuation_card_id:
        raise ValueError('Saved split continuation identity differs from its presentation')
    if card_id(override.shortened_start_fen, (*override.shortened_moves, *override.continuation_moves)) != override.source_card_id:
        raise ValueError('Saved split children do not reproduce the source identity')
    board = chess.Board(override.shortened_start_fen)
    for move_uci in override.shortened_moves:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves: raise ValueError('Saved split has an illegal move')
        board.push(move)
    if board.fen().split()[:4] != override.continuation_start_fen.split()[:4]:
        raise ValueError('Saved split children are not connected')
    for move_uci in override.continuation_moves:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves: raise ValueError('Saved split has an illegal move')
        board.push(move)


def _payload(start_fen, moves, trained_color):
    return (' '.join(start_fen.split()[:4]), tuple(moves), trained_color)


def _step_projection(step: GraphStep) -> dict:
    return {**asdict(step), 'moves': list(step.moves), 'decision_fen_keys': list(step.decision_fen_keys)}


def _presentation(steps, repertoire_id, decisions_by_card) -> Iterator[None]:
    steps = ordered_steps(steps)
    representatives = {}
    roles_by_card = {}
    lines_by_card = {}
    for step in steps:
        previous = representatives.setdefault(step.card_id, step)
        if _payload(previous.starting_fen, previous.moves, previous.trained_color) != _payload(step.starting_fen, step.moves, step.trained_color):
            raise PrefixEvaluationError('unsupported_source', 'One card identity has incompatible presentations or trained colors.')
        roles_by_card.setdefault(step.card_id, set()).add(step.segment_kind)
        lines_by_card.setdefault(step.card_id, set()).add(step.line_id)
    cards = []
    decision_card_counts = Counter()
    occurrence_count = within_card_repetitions = 0
    for identifier, step in sorted(representatives.items()):
        if identifier not in decisions_by_card:
            board = chess.Board(step.starting_fen)
            occurrences = []
            for move_uci in step.moves:
                if board.turn == (step.trained_color == 'white'):
                    occurrences.append(decision_identity(repertoire_id, board.fen(), step.trained_color, move_uci))
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves: raise ValueError('Graph presentation contains an illegal move')
                board.push(move)
            if len(occurrences) != len(step.decision_fen_keys):
                raise ValueError('Graph decision metadata differs from its presentation')
            decisions_by_card[identifier] = tuple(occurrences)
        occurrences = decisions_by_card[identifier]
        occurrence_count += len(occurrences)
        within_card_repetitions += len(occurrences) - len(set(occurrences))
        decision_card_counts.update(set(occurrences))
        cards.append({'card_id': identifier, 'starting_fen': step.starting_fen, 'moves': list(step.moves),
                      'trained_color': step.trained_color, 'roles': sorted(roles_by_card[identifier]),
                      'line_ids': sorted(lines_by_card[identifier]), 'decision_ids': list(occurrences)})
        yield
    return {'metrics': {'distinct_cards': len(cards),
                        'prefix_cards': sum('prefix' in roles for roles in roles_by_card.values()),
                        'descendant_decision_cards': sum('decision' in roles for roles in roles_by_card.values()),
                        'learner_decision_occurrences': occurrence_count,
                        'distinct_learner_decisions': len(decision_card_counts),
                        'repeated_decisions_across_cards': sum(count - 1 for count in decision_card_counts.values()),
                        'repeated_decisions_within_cards': within_card_repetitions,
                        'board_starts': len(cards)},
            'cards': cards, 'steps': [_step_projection(step) for step in steps]}


def _comparison(current, proposed):
    current_ids = {card['card_id'] for card in current['cards']}
    proposed_ids = {card['card_id'] for card in proposed['cards']}
    start_delta = len(proposed_ids) - len(current_ids)
    return {'current': current, 'proposed': proposed,
            'delta': {key: proposed['metrics'][key] - value for key, value in current['metrics'].items()},
            'additional_starts': max(0, start_delta), 'reduced_starts': max(0, -start_delta),
            'unchanged_card_ids': sorted(current_ids & proposed_ids),
            'added_card_ids': sorted(proposed_ids - current_ids), 'removed_card_ids': sorted(current_ids - proposed_ids)}


def iter_prefix_evaluation(snapshot: EvaluationSnapshot, selected_line_ids, candidate_depths) -> Iterator[None]:
    """Yield between bounded line/card calculations; the caller owns admission/time."""
    validate_source(snapshot)
    selected = tuple(sorted(selected_line_ids))
    lines_by_id = {line.id: line for line in snapshot.lines}
    if len(set(selected)) != len(selected) or not set(selected) <= lines_by_id.keys():
        raise PrefixEvaluationError('invalid_selection', 'Select unique source line IDs from this snapshot.')
    if candidate_depths is not None and (set(candidate_depths) != set(selected) or
            any(type(depth) is not int or not 1 <= depth <= 20 for depth in candidate_depths.values())):
        raise PrefixEvaluationError('invalid_selection', 'Candidate depths must cover exactly the selected lines with integers from 1 to 20.')
    candidate_depths = dict(candidate_depths) if candidate_depths is not None else None
    try:
        overrides = tuple(override.graph_override() for override in snapshot.prefix_overrides)
        for override in snapshot.prefix_overrides:
            _validate_override(override)
            yield
        current_by_line = {}
        proposed_by_line = {}
        current_steps = []
        proposed_steps = []
        for line in sorted(snapshot.lines, key=lambda line: line.id):
            current = build_graph(GraphInput(snapshot.repertoire_id, (line.graph_line(),), 1, overrides))
            current_by_line[line.id] = current
            current_steps.extend(current)
            if len(current_steps) > MAX_GRAPH_STEPS:
                raise PrefixEvaluationError('limit_exceeded', 'Current source exceeds the bounded graph evaluation limit.')
            yield
        if ordered_steps(current_steps) != ordered_steps(snapshot.published_steps):
            raise PrefixEvaluationError('graph_not_ready', 'Saved source and published graph differ. Wait for graph publication or repair the source.')
        presentations_by_id = {card.id: card for card in snapshot.presentations}
        if snapshot.presentations:
            for step in current_steps:
                card = presentations_by_id.get(step.card_id)
                if card is None or card.archived or not card.linked or _payload(card.start_fen, card.moves, card.trained_color) != _payload(step.starting_fen, step.moves, step.trained_color):
                    raise PrefixEvaluationError('graph_not_ready', 'A published card changed or lost its source membership. Refresh after publication.')
        for line in sorted(snapshot.lines, key=lambda line: line.id):
            depth = candidate_depths[line.id] if candidate_depths is not None and line.id in selected else line.saved_depth
            proposed = current_by_line[line.id] if depth == line.saved_depth else build_graph(
                GraphInput(snapshot.repertoire_id, (replace(line, saved_depth=depth).graph_line(),), 1, overrides))
            proposed_by_line[line.id] = proposed
            proposed_steps.extend(proposed)
            if len(proposed_steps) > MAX_GRAPH_STEPS:
                raise PrefixEvaluationError('limit_exceeded', 'Candidate exceeds the bounded graph evaluation limit.')
            yield
        decisions_by_card = {}
        selected_current = yield from _presentation((step for step in current_steps if step.line_id in selected), snapshot.repertoire_id, decisions_by_card)
        selected_proposed = yield from _presentation((step for step in proposed_steps if step.line_id in selected), snapshot.repertoire_id, decisions_by_card)
        whole_current = yield from _presentation(current_steps, snapshot.repertoire_id, decisions_by_card)
        whole_proposed = yield from _presentation(proposed_steps, snapshot.repertoire_id, decisions_by_card)
    except PrefixEvaluationError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise PrefixEvaluationError('unsupported_source', f'Cannot evaluate saved source: {error}') from error
    changed_depths = any(candidate_depths is not None and candidate_depths[identifier] != lines_by_id[identifier].saved_depth for identifier in selected)
    changed_structure = ordered_steps(current_steps) != ordered_steps(proposed_steps)
    distribution = Counter(lines_by_id[identifier].saved_depth for identifier in selected)
    return {'version': EVALUATION_VERSION, 'preview_only': True, 'estimate_basis': ESTIMATE_BASIS,
            'repertoire_id': snapshot.repertoire_id, 'graph_generation': snapshot.graph_generation,
            'snapshot_id': snapshot_identity(snapshot), 'selected_line_ids': list(selected),
            'selected_line_count': len(selected),
            'status': 'empty_selection' if not selected else 'changed' if changed_structure else 'no_change',
            'depth_configuration_changed': changed_depths, 'structure_changed': changed_structure,
            'current_depth_distribution': [{'depth': depth, 'line_count': count} for depth, count in sorted(distribution.items())],
            'line_depths': [{'line_id': identifier, 'current_depth': lines_by_id[identifier].saved_depth,
                            'requested_depth': candidate_depths[identifier] if candidate_depths is not None else lines_by_id[identifier].saved_depth,
                            'current_effective_depth': len(current_by_line[identifier][0].decision_fen_keys) if current_by_line[identifier] else 0,
                            'proposed_effective_depth': len(proposed_by_line[identifier][0].decision_fen_keys) if proposed_by_line[identifier] else 0} for identifier in selected],
            'selected': _comparison(selected_current, selected_proposed),
            'whole_repertoire': _comparison(whole_current, whole_proposed)}


def evaluate_prefix(snapshot, selected_line_ids, candidate_depths) -> dict:
    calculation = iter_prefix_evaluation(snapshot, selected_line_ids, candidate_depths)
    while True:
        try: next(calculation)
        except StopIteration as completed: return completed.value
