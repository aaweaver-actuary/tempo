"""Hybrid opening graph construction with cumulative roots and decision descendants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
import sqlite3
from time import perf_counter

import chess
from fsrs import Card, State

from app.chess.cards import card_id
from ..contracts.opening_graph import GraphStep
from tempo_legacy_database.adapters.opening_graph import compatibility as storage


@dataclass(frozen=True)
class DecisionSegment:
    card_id: str
    decision_index: int
    segment_kind: str
    first_decision_index: int
    last_decision_index: int
    decision_fen_keys: tuple[str, ...]
    starting_fen: str
    moves: tuple[str, ...]
    parent_card_id: str | None
    trained_color: str


@dataclass(frozen=True)
class GraphInput:
    repertoire_id: str
    lines: tuple[dict, ...]
    default_depth: int
    prefix_overrides: tuple[dict, ...] = ()


@dataclass(frozen=True)
class GraphRebuildInput:
    graph_input: GraphInput
    legacy_cards: tuple[dict, ...]
    reviews_by_card: dict[str, list[dict]]
    existing_card_ids: frozenset[str]
    study_day: str
    deleted_card_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GraphRebuildArtifacts:
    graph_steps: tuple[GraphStep, ...]
    legacy_mappings: tuple[tuple[str, str], ...]
    schedule_seeds: dict[str, dict]


def build_graph(graph_input: GraphInput) -> tuple[GraphStep, ...]:
    steps: list[GraphStep] = []
    override_by_source = {
        override["source_card_id"]: override
        for override in graph_input.prefix_overrides
    }
    for line in graph_input.lines:
        maximum_decisions = int(line.get("learner_decision_count") or graph_input.default_depth)
        segments = decision_segments(
            line["start_fen"],
            tuple(json.loads(line["moves_json"])),
            line["trained_color"],
            maximum_decisions,
        )
        expanded_segments: list[DecisionSegment] = []
        parent_card_id: str | None = None
        for segment in segments:
            for expanded_segment in _expanded_segment(segment, override_by_source):
                expanded_segment = replace(
                    expanded_segment,
                    decision_index=len(expanded_segments),
                    parent_card_id=parent_card_id,
                )
                expanded_segments.append(expanded_segment)
                parent_card_id = expanded_segment.card_id
        for segment in expanded_segments:
            steps.append(GraphStep(
                repertoire_id=graph_input.repertoire_id,
                line_id=line["id"],
                decision_index=segment.decision_index,
                segment_kind=segment.segment_kind,
                first_decision_index=segment.first_decision_index,
                last_decision_index=segment.last_decision_index,
                decision_fen_keys=segment.decision_fen_keys,
                card_id=segment.card_id,
                parent_card_id=segment.parent_card_id,
                decision_fen_key=segment.decision_fen_keys[-1],
                starting_fen=segment.starting_fen,
                moves=segment.moves,
                trained_color=segment.trained_color,
            ))
    return tuple(steps)


def _decision_fen_keys(
    starting_fen: str, moves: tuple[str, ...], trained_color: str
) -> tuple[str, ...]:
    board = chess.Board(starting_fen)
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    decision_fen_keys: list[str] = []
    for move_uci in moves:
        if board.turn == trained_chess_color:
            decision_fen_keys.append(" ".join(board.fen().split()[:4]))
        board.push_uci(move_uci)
    return tuple(decision_fen_keys)


def _expanded_segment(
    segment: DecisionSegment, override_by_source: dict[str, dict]
) -> tuple[DecisionSegment, ...]:
    override = override_by_source.get(segment.card_id)
    if segment.segment_kind != "prefix" or override is None:
        return (segment,)

    shortened_moves = tuple(json.loads(override["shortened_moves_json"]))
    continuation_moves = tuple(json.loads(override["continuation_moves_json"]))
    shortened_fen_keys = _decision_fen_keys(
        override["shortened_start_fen"], shortened_moves, segment.trained_color
    )
    continuation_fen_keys = _decision_fen_keys(
        override["continuation_start_fen"], continuation_moves, segment.trained_color
    )
    if not shortened_fen_keys or len(continuation_fen_keys) != 1:
        raise ValueError("Saved prefix split is not a valid hybrid graph override")
    shortened_segment = DecisionSegment(
        card_id=override["shortened_card_id"],
        decision_index=segment.decision_index,
        segment_kind="prefix",
        first_decision_index=segment.first_decision_index,
        last_decision_index=(
            segment.first_decision_index + len(shortened_fen_keys) - 1
        ),
        decision_fen_keys=shortened_fen_keys,
        starting_fen=override["shortened_start_fen"],
        moves=shortened_moves,
        parent_card_id=segment.parent_card_id,
        trained_color=segment.trained_color,
    )
    expanded_shortened = _expanded_segment(shortened_segment, override_by_source)
    continuation_decision_index = segment.first_decision_index + len(
        shortened_fen_keys
    )
    continuation_segment = DecisionSegment(
        card_id=override["continuation_card_id"],
        decision_index=segment.decision_index + len(expanded_shortened),
        segment_kind="decision",
        first_decision_index=continuation_decision_index,
        last_decision_index=continuation_decision_index,
        decision_fen_keys=continuation_fen_keys,
        starting_fen=override["continuation_start_fen"],
        moves=continuation_moves,
        parent_card_id=expanded_shortened[-1].card_id,
        trained_color=segment.trained_color,
    )
    return (*expanded_shortened, continuation_segment)


def decision_segments(
    starting_fen: str,
    moves_uci: list[str] | tuple[str, ...],
    trained_color: str,
    maximum_decisions: int,
) -> tuple[DecisionSegment, ...]:
    """Build one cumulative route prefix followed by one-decision descendants."""

    if trained_color not in {"white", "black"}:
        raise ValueError("trained_color must be white or black")
    if maximum_decisions < 0:
        raise ValueError("maximum_decisions must be nonnegative")

    board = chess.Board(starting_fen)
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    legal_moves: list[str] = []
    learner_move_offsets: list[int] = []
    learner_decision_fen_keys: list[str] = []

    for move_uci in moves_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal repertoire move {move_uci}")
        if board.turn == trained_chess_color:
            learner_move_offsets.append(len(legal_moves))
            learner_decision_fen_keys.append(" ".join(board.fen().split()[:4]))
        legal_moves.append(move_uci)
        board.push(move)

    if not learner_move_offsets or maximum_decisions == 0:
        return ()

    prefix_decision_count = min(maximum_decisions, len(learner_move_offsets))
    prefix_end_offset = learner_move_offsets[prefix_decision_count - 1] + 1
    prefix_moves = tuple(legal_moves[:prefix_end_offset])
    prefix_identifier = card_id(starting_fen, prefix_moves)
    segments: list[DecisionSegment] = []
    segments.append(
        DecisionSegment(
            card_id=prefix_identifier,
            decision_index=0,
            segment_kind="prefix",
            first_decision_index=0,
            last_decision_index=prefix_decision_count - 1,
            decision_fen_keys=tuple(
                learner_decision_fen_keys[:prefix_decision_count]
            ),
            starting_fen=starting_fen,
            moves=prefix_moves,
            parent_card_id=None,
            trained_color=trained_color,
        )
    )

    board = chess.Board(starting_fen)
    for move_uci in legal_moves[:prefix_end_offset]:
        board.push_uci(move_uci)
    segment_starting_fen = board.fen()
    segment_moves: list[str] = []
    parent_card_id = prefix_identifier
    learner_decision_index = prefix_decision_count

    for move_uci in legal_moves[prefix_end_offset:]:
        moving_color = board.turn
        decision_fen_key = " ".join(board.fen().split()[:4])
        segment_moves.append(move_uci)
        board.push_uci(move_uci)
        if moving_color != trained_chess_color:
            continue
        identifier = card_id(segment_starting_fen, segment_moves)
        segments.append(
            DecisionSegment(
                card_id=identifier,
                decision_index=len(segments),
                segment_kind="decision",
                first_decision_index=learner_decision_index,
                last_decision_index=learner_decision_index,
                decision_fen_keys=(decision_fen_key,),
                starting_fen=segment_starting_fen,
                moves=tuple(segment_moves),
                parent_card_id=parent_card_id,
                trained_color=trained_color,
            )
        )
        learner_decision_index += 1
        parent_card_id = identifier
        segment_starting_fen = board.fen()
        segment_moves = []

    return tuple(segments)


def enqueue_opening_graph_rebuild(
    repertoire_id: str,
    *,
    foreground: bool = True,
    local_day: str | None = None,
) -> dict:
    from .durable_tasks import enqueue_task

    return enqueue_task(
        "opening_graph_rebuild",
        repertoire_id,
        {
            "repertoire_id": repertoire_id,
            "local_day": local_day or date.today().isoformat(),
        },
        priority=40,
        foreground=foreground,
    )


def load_graph_input(repertoire_id: str) -> GraphInput:
    from tempo_legacy_database.connections import read_connection

    with read_connection() as database:
        lines = tuple(
            dict(row)
            for row in storage.source_line_rows(database, (repertoire_id,))
        )
        settings = storage.initial_depth_row(database)
        prefix_overrides = tuple(
            dict(row)
            for row in storage.prefix_override_rows(database)
        )
    return GraphInput(
        repertoire_id,
        lines,
        int(settings[0]),
        prefix_overrides,
    )


def _load_legacy_snapshot(repertoire_id: str) -> tuple[tuple[dict, ...], dict[str, list[dict]], set[str]]:
    from tempo_legacy_database.connections import read_connection

    with read_connection() as database:
        cards = tuple(
            dict(row)
            for row in storage.legacy_card_rows(database, (repertoire_id,))
        )
        reviews: dict[str, list[dict]] = {}
        for row in storage.legacy_review_rows(database, (repertoire_id,)):
            reviews.setdefault(row["card_id"], []).append(dict(row))
        existing_card_ids = {
            row[0]
            for row in storage.existing_card_rows(database)
        }
    return cards, reviews, existing_card_ids


def _trained_decisions(card: dict) -> set[tuple[str, str]]:
    try:
        board = chess.Board(card["start_fen"])
        moves = json.loads(card["moves_json"])
        trained_color = chess.WHITE if card["trained_color"] == "white" else chess.BLACK
    except (ValueError, TypeError, json.JSONDecodeError):
        return set()
    decisions: set[tuple[str, str]] = set()
    for move_uci in moves:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            return set()
        if board.turn == trained_color:
            decisions.add((" ".join(board.fen().split()[:4]), move_uci))
        board.push(move)
    return decisions


def _legacy_mappings(
    graph_steps: tuple[GraphStep, ...], legacy_cards: tuple[dict, ...]
) -> tuple[tuple[str, str], ...]:
    decisions_by_card = {card["id"]: _trained_decisions(card) for card in legacy_cards}
    decisions_by_step = {
        step.card_id: _graph_step_decisions(step) for step in graph_steps
    }
    return tuple(
        sorted(
            {
                (legacy_card_id, step.card_id)
                for legacy_card_id, decisions in decisions_by_card.items()
                for step in graph_steps
                if decisions.intersection(decisions_by_step[step.card_id])
            }
        )
    )


def _graph_step_decisions(step: GraphStep) -> set[tuple[str, str]]:
    board = chess.Board(step.starting_fen)
    trained_color = chess.WHITE if step.trained_color == "white" else chess.BLACK
    decisions: set[tuple[str, str]] = set()
    for move_uci in step.moves:
        if board.turn == trained_color:
            decisions.add((" ".join(board.fen().split()[:4]), move_uci))
        board.push_uci(move_uci)
    return decisions


def _seed_values(
    graph_steps: tuple[GraphStep, ...],
    legacy_cards: tuple[dict, ...],
    reviews_by_card: dict[str, list[dict]],
    existing_card_ids: set[str],
    study_day: date,
) -> dict[str, dict]:
    legacy_by_id = {card["id"]: card for card in legacy_cards}
    candidates_by_trained_decision: dict[tuple[str, str], list[dict]] = {}
    for legacy_card in legacy_cards:
        legacy_card_id = legacy_card["id"]
        successful_reviews = [
            review
            for review in reviews_by_card.get(legacy_card_id, [])
            if review["rating"] == "correct"
        ]
        if not successful_reviews and not legacy_card.get("first_correct_at"):
            continue
        candidate = {"card": legacy_by_id[legacy_card_id], "reviews": successful_reviews}
        for trained_decision in _trained_decisions(legacy_card):
            candidates_by_trained_decision.setdefault(trained_decision, []).append(
                candidate
            )

    today = study_day
    latest_verification = today + timedelta(days=7)
    seeds: dict[str, dict] = {}
    unique_steps = {step.card_id: step for step in graph_steps}
    for decision_card_id, graph_step in unique_steps.items():
        if decision_card_id in existing_card_ids:
            continue
        required_decisions = _graph_step_decisions(graph_step)
        candidates_by_required_decision = [
            candidates_by_trained_decision.get(required_decision, [])
            for required_decision in required_decisions
        ]
        positive_candidates = [
            candidate
            for candidates in candidates_by_required_decision
            for candidate in candidates
        ]
        if not positive_candidates:
            continue
        all_decisions_mature = all(
            any(candidate["card"]["state"] == "mature" for candidate in candidates)
            for candidates in candidates_by_required_decision
        )
        qualifying = (
            [
                candidate
                for candidates in candidates_by_required_decision
                for candidate in candidates
                if candidate["card"]["state"] == "mature"
            ]
            if all_decisions_mature
            else positive_candidates
        )
        chosen = min(
            qualifying,
            key=lambda candidate: (
                float(candidate["card"]["stability"] or 0),
                candidate["card"]["due_date"],
                candidate["card"]["id"],
            ),
        )
        source = chosen["card"]
        source_reviews = chosen["reviews"]
        successful_days = max(
            int(source.get("seed_successful_days") or 0),
            len({review["reviewed_at"][:10] for review in source_reviews}),
        )
        recent_clean = max(
            int(source.get("seed_recent_clean") or 0), min(2, len(source_reviews))
        )
        verification_due = min(
            min(date.fromisoformat(candidate["card"]["due_date"]) for candidate in qualifying),
            latest_verification,
        )
        stability = min(
            14.0,
            min(float(candidate["card"]["stability"] or 0) for candidate in qualifying),
        )
        if source["fsrs_card_json"]:
            fsrs_card = Card.from_json(source["fsrs_card_json"])
        else:
            fsrs_card = Card()
        fsrs_card.state = State.Review if all_decisions_mature else State.Learning
        fsrs_card.stability = stability or fsrs_card.stability
        fsrs_card.due = datetime.combine(verification_due, time(hour=12), timezone.utc)
        seeds[decision_card_id] = {
            "source_card_id": source["id"],
            "state": "mature" if all_decisions_mature else "learning",
            "due_date": verification_due.isoformat(),
            "interval_days": min(7, max(0, int(source["interval_days"] or 0))),
            "stability": stability,
            "fsrs_card_json": fsrs_card.to_json(),
            "first_correct_at": source["first_correct_at"] or source_reviews[-1]["reviewed_at"],
            "introduced_at": source["introduced_at"] or today.isoformat(),
            "baseline_successful_days": 3 if all_decisions_mature else min(2, successful_days),
            "baseline_recent_clean": 2 if all_decisions_mature else recent_clean,
            "verification_due": verification_due.isoformat(),
        }
    return seeds


def _chunks(values: tuple[GraphStep, ...], size: int = 250):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def prepare_opening_graph_rebuild(task: dict) -> GraphRebuildInput:
    """Copy immutable rebuild inputs while holding only short read connections."""

    repertoire_id = task["payload"]["repertoire_id"]
    local_day = task["payload"].get("local_day") or date.today().isoformat()
    graph_input = load_graph_input(repertoire_id)
    legacy_cards, reviews_by_card, existing_card_ids = _load_legacy_snapshot(repertoire_id)
    from tempo_legacy_database.connections import background_read_connection
    with background_read_connection() as database:
        deleted_ids = frozenset(row[0] for row in storage.deleted_card_rows(database))
    return GraphRebuildInput(
        graph_input=graph_input,
        legacy_cards=legacy_cards,
        reviews_by_card=reviews_by_card,
        existing_card_ids=frozenset(existing_card_ids),
        study_day=local_day,
        deleted_card_ids=deleted_ids,
    )


def calculate_opening_graph_artifacts(
    rebuild_input: GraphRebuildInput,
) -> GraphRebuildArtifacts:
    """Perform chess traversal and migration scoring without SQLite access."""

    from ..card_deletion import omit_deleted_graph_steps
    graph_steps = omit_deleted_graph_steps(build_graph(rebuild_input.graph_input), set(rebuild_input.deleted_card_ids))
    legacy_mappings = _legacy_mappings(graph_steps, rebuild_input.legacy_cards)
    schedule_seeds = _seed_values(
        graph_steps,
        rebuild_input.legacy_cards,
        rebuild_input.reviews_by_card,
        set(rebuild_input.existing_card_ids),
        date.fromisoformat(rebuild_input.study_day),
    )
    return GraphRebuildArtifacts(
        graph_steps=graph_steps,
        legacy_mappings=legacy_mappings,
        schedule_seeds=schedule_seeds,
    )


def publish_opening_graph_rebuild(
    task: dict, artifacts: GraphRebuildArtifacts
) -> None:
    """Stage bounded writes and atomically publish a current graph generation."""

    from tempo_legacy_database.adapters.database_executor import submit_background_write

    repertoire_id = task["payload"]["repertoire_id"]
    local_day = task["payload"].get("local_day") or date.today().isoformat()
    generation = int(task["generation"])
    graph_steps = artifacts.graph_steps
    legacy_mappings = artifacts.legacy_mappings
    schedule_seeds = artifacts.schedule_seeds

    def current_task(database):
        return storage.leased_task(database, (task["id"], generation, task["lease_token"])) is not None

    def clear_staging(database: sqlite3.Connection) -> None:
        if not current_task(database):
            return
        storage.clear_staging(database, (repertoire_id, generation))

    submit_background_write(
        clear_staging,
        label=f"opening-graph-clear:{repertoire_id}:{generation}",
    )
    for graph_step_chunk in _chunks(graph_steps):
        def stage(
            database: sqlite3.Connection,
            values: tuple[GraphStep, ...] = graph_step_chunk,
        ) -> None:
            if not current_task(database):
                return
            from ..card_deletion import omit_deleted_graph_steps
            deleted_ids = {row[0] for row in storage.deleted_card_rows(database)}
            retained_steps = {(step.line_id, step.decision_index): step for step in omit_deleted_graph_steps(graph_steps, deleted_ids)}
            values = tuple(retained_steps[(step.line_id, step.decision_index)] for step in values if (step.line_id, step.decision_index) in retained_steps)
            storage.insert_steps(database, [
                    (
                        step.repertoire_id,
                        generation,
                        step.line_id,
                        step.decision_index,
                        step.segment_kind,
                        step.first_decision_index,
                        step.last_decision_index,
                        json.dumps(step.decision_fen_keys),
                        step.card_id,
                        step.parent_card_id,
                        step.decision_fen_key,
                        step.starting_fen,
                        json.dumps(step.moves),
                        step.trained_color,
                    )
                    for step in values
                ])
            storage.insert_cards(database, [
                    (
                        step.card_id,
                        repertoire_id,
                        step.segment_kind if step.segment_kind == "prefix" else "response",
                        step.starting_fen,
                        json.dumps(step.moves),
                        "new" if step.parent_card_id is None else "locked",
                        local_day,
                        step.trained_color,
                    )
                    for step in values
                ])

        submit_background_write(
            stage,
            label=f"opening-graph-stage:{repertoire_id}:{generation}",
        )

    for mapping_offset in range(0, len(legacy_mappings), 500):
        mapping_chunk = legacy_mappings[mapping_offset : mapping_offset + 500]

        def stage_mappings(database: sqlite3.Connection, values=mapping_chunk) -> None:
            if not current_task(database):
                return
            values = tuple((source, target) for source, target in values if storage.card_present(database, (source,)) and storage.card_present(database, (target,)))
            storage.insert_legacy_mappings(database, [
                    (repertoire_id, generation, legacy_card_id, decision_card_id)
                    for legacy_card_id, decision_card_id in values
                ])

        submit_background_write(
            stage_mappings,
            label=f"opening-graph-map:{repertoire_id}:{generation}",
        )

    seed_items = tuple(schedule_seeds.items())
    for seed_offset in range(0, len(seed_items), 100):
        seed_chunk = seed_items[seed_offset : seed_offset + 100]

        def apply_seeds(database: sqlite3.Connection, values=seed_chunk) -> None:
            if not current_task(database):
                return
            now = datetime.now(timezone.utc).isoformat()
            for decision_card_id, seed in values:
                if not storage.card_present(database, (decision_card_id,)):
                    continue
                storage.apply_seed(database, (
                        seed["state"], seed["due_date"], seed["interval_days"],
                        seed["stability"], seed["fsrs_card_json"],
                        seed["first_correct_at"], seed["introduced_at"],
                        decision_card_id,
                    ))
                storage.insert_seed(database, (
                        decision_card_id, seed["source_card_id"],
                        seed["baseline_successful_days"], seed["baseline_recent_clean"],
                        seed["verification_due"], now,
                    ))

        submit_background_write(
            apply_seeds,
            label=f"opening-graph-seed:{repertoire_id}:{generation}",
        )

    def publish(database: sqlite3.Connection) -> bool:
        current = storage.task_generation(database, (task["id"], task["lease_token"]))
        if not current or int(current["generation"]) != generation:
            return False
        now = datetime.now(timezone.utc).isoformat()
        storage.publish_generation(database, (repertoire_id, generation, now))
        storage.insert_links(database, (repertoire_id, generation))
        storage.classify_generated_cards(database, (repertoire_id, generation))
        storage.lock_unintroduced_descendants(database)
        storage.unlock_unintroduced_roots(database, (local_day,))
        storage.retire_obsolete_queue(database, (repertoire_id, repertoire_id, generation))
        # Retire an orphaned generated owner association before deleting its
        # link, retaining history without reactivating owner fallback.
        storage.archive_generated_owner(database, (repertoire_id, repertoire_id, repertoire_id, repertoire_id, generation))
        # Reassign retained owners before deleting generated memberships, so
        # cleanup cannot reactivate a stale owner fallback.
        storage.reassign_retained_owner(database, (repertoire_id, repertoire_id, repertoire_id, repertoire_id, repertoire_id, generation))
        storage.delete_generated_links(database, (repertoire_id, repertoire_id, generation))
        storage.archive_unlinked_generated_cards(database)
        storage.delete_blocks(database, (repertoire_id,))
        storage.insert_blocks(database, (repertoire_id, f"graph:{generation}", now, repertoire_id, generation))
        storage.refresh_validation(database, (repertoire_id, generation))
        return True

    published = submit_background_write(
        publish,
        label=f"opening-graph-publish:{repertoire_id}:{generation}",
    )
    if not published:
        return
    from .repertoire_integrity import enqueue_integrity_scans

    enqueue_integrity_scans(
        repertoire_id,
        restart=True,
        background=True,
    )
    from .durable_tasks import enqueue_task

    enqueue_task(
        "daily_queue",
        local_day,
        {"queue_date": local_day},
        priority=10,
        foreground=False,
    )
    from .repertoire_opportunities import enqueue_opportunity_refresh

    enqueue_opportunity_refresh(repertoire_id, background=True)


def execute_opening_graph_rebuild(task: dict) -> None:
    """Run the registered durable rebuild handler across short read and write phases."""

    started_at = perf_counter()
    rebuild_input = prepare_opening_graph_rebuild(task)
    prepared_at = perf_counter()
    artifacts = calculate_opening_graph_artifacts(rebuild_input)
    calculated_at = perf_counter()
    publish_opening_graph_rebuild(task, artifacts)
    published_at = perf_counter()
    logging.getLogger("tempo.opening_graph").info(
        "graph rebuild repertoire_id=%s generation=%s graph_steps=%d "
        "prepare_ms=%.3f compute_ms=%.3f publish_ms=%.3f total_ms=%.3f",
        task["payload"]["repertoire_id"], task["generation"], len(artifacts.graph_steps),
        (prepared_at - started_at) * 1000,
        (calculated_at - prepared_at) * 1000,
        (published_at - calculated_at) * 1000,
        (published_at - started_at) * 1000,
    )
