"""Pure, bounded assessment of authored study answers."""

from __future__ import annotations

import chess

from ..study_contracts import (
    ChoiceAnswer, ChoiceExercise, ExplanationAnswer, ExplanationExercise,
    KnightAnswer, KnightExercise, MoveAnswer, MoveExercise, SquareAnswer,
    SquareExercise,
)


GRADER_VERSION = 1


def _square(square: str) -> int:
    try:
        if len(square) != 2:
            raise ValueError(square)
        return chess.parse_square(square)
    except ValueError as error:
        raise ValueError(f"Invalid square: {square}") from error


def validate_exercise(specification, fen: str) -> None:
    board = chess.Board(fen)
    if not board.is_valid():
        raise ValueError("Exercise position is not a valid chess board")
    if isinstance(specification, MoveExercise):
        for line in specification.accepted_lines:
            if not line or (specification.mode == "single" and len(line) != 1):
                raise ValueError("Accepted lines must fit the selected move mode")
            position = board.copy(stack=True)
            for move_text in line:
                try:
                    move = chess.Move.from_uci(move_text)
                except ValueError as error:
                    raise ValueError(f"Invalid UCI move: {move_text}") from error
                if move not in position.legal_moves:
                    raise ValueError(f"Illegal accepted move: {move_text}")
                position.push(move)
    elif isinstance(specification, SquareExercise):
        required = {_square(value) for value in specification.required}
        optional = {_square(value) for value in specification.optional}
        if len(required) != len(specification.required) or len(optional) != len(specification.optional) or required & optional:
            raise ValueError("Square rubric contains duplicates")
        if specification.candidate_region is not None:
            region = {_square(value) for value in specification.candidate_region}
            if len(region) != len(specification.candidate_region) or not required | optional <= region:
                raise ValueError("Candidate region must contain every accepted square")
    elif isinstance(specification, KnightExercise):
        start = _square(specification.start_square)
        piece = board.piece_at(start)
        if piece is None or piece.piece_type != chess.KNIGHT:
            raise ValueError("The designated starting square must contain a knight")
        if specification.minimum_hops > specification.maximum_hops:
            raise ValueError("Minimum hops exceeds maximum hops")
        targets = {_square(value) for value in specification.target_squares}
        if len(targets) != len(specification.target_squares):
            raise ValueError("Target squares must be unique")
    elif isinstance(specification, ChoiceExercise):
        option_ids = [option.id for option in specification.options]
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("Choice option IDs must be unique")
        if len(set(specification.correct_option_ids)) != len(specification.correct_option_ids) or not set(specification.correct_option_ids) <= set(option_ids):
            raise ValueError("Correct option IDs must identify authored choices")
    elif not isinstance(specification, ExplanationExercise):
        raise ValueError("Unknown exercise specification")


def _knight_landings(board: chess.Board, square: int, start: int):
    for destination in chess.SQUARES:
        if destination != square and chess.square_distance(square, destination) in (1, 2):
            file_delta = abs(chess.square_file(square) - chess.square_file(destination))
            rank_delta = abs(chess.square_rank(square) - chess.square_rank(destination))
            if sorted((file_delta, rank_delta)) == [1, 2] and (destination == start or board.piece_at(destination) is None):
                yield destination


def _valid_knight_destination(square: int, targets: set[int]) -> bool:
    return targets <= set(chess.SquareSet(chess.BB_KNIGHT_ATTACKS[square]))


def available_knight_routes(specification: KnightExercise, fen: str) -> list[list[str]]:
    board = chess.Board(fen)
    start = _square(specification.start_square)
    targets = {_square(value) for value in specification.target_squares}
    routes: list[list[str]] = []

    def visit(square: int, route: list[int]) -> None:
        hops = len(route) - 1
        eligible_depth = hops == specification.maximum_hops if specification.hop_rule == "exact" else hops >= specification.minimum_hops
        if eligible_depth and _valid_knight_destination(square, targets):
            routes.append([chess.square_name(value) for value in route])
        if hops >= specification.maximum_hops:
            return
        for destination in _knight_landings(board, square, start):
            visit(destination, [*route, destination])

    visit(start, [start])
    return routes


def evaluate_answer(specification, answer, fen: str) -> dict:
    """Return an outcome without persistence, scheduling, or network work."""
    if specification.type != answer.type:
        return {"outcome": "invalid_submission", "feedback": "Answer type does not match this exercise"}
    if isinstance(specification, MoveExercise) and isinstance(answer, MoveAnswer):
        position = chess.Board(fen)
        for move_text in answer.moves:
            try:
                move = chess.Move.from_uci(move_text)
            except ValueError:
                return {"outcome": "invalid_submission", "feedback": "Use legal UCI moves"}
            if move not in position.legal_moves:
                return {"outcome": "invalid_submission", "feedback": "The submitted move is illegal"}
            position.push(move)
        if specification.mode == "single" and len(answer.moves) != 1:
            return {"outcome": "invalid_submission", "feedback": "Submit one move"}
        if answer.moves in specification.accepted_lines:
            return {"outcome": "correct", "feedback": "Matches an authored answer"}
        if specification.grading_policy == "open_judgment":
            return {"outcome": "unrecognized", "feedback": "Legal move outside the authored reference answers; assess after reveal"}
        return {"outcome": "incorrect", "feedback": "Does not match an accepted reference continuation"}
    if isinstance(specification, SquareExercise) and isinstance(answer, SquareAnswer):
        try:
            selected = {_square(value) for value in answer.squares}
        except ValueError as error:
            return {"outcome": "invalid_submission", "feedback": str(error)}
        if len(selected) != len(answer.squares):
            return {"outcome": "invalid_submission", "feedback": "Select each square once"}
        region = {_square(value) for value in specification.candidate_region} if specification.candidate_region is not None else set(chess.SQUARES)
        required = {_square(value) for value in specification.required}
        accepted = required | {_square(value) for value in specification.optional}
        omitted = sorted(chess.square_name(value) for value in required - selected)
        extra = sorted(chess.square_name(value) for value in selected - accepted)
        outside = sorted(chess.square_name(value) for value in selected - region)
        return {"outcome": "correct" if not omitted and not extra and not outside else "incorrect",
                "feedback": "Square selection assessed against the authored criterion",
                "omitted": omitted, "extra": extra, "outside_region": outside}
    if isinstance(specification, KnightExercise) and isinstance(answer, KnightAnswer):
        routes = available_knight_routes(specification, fen)
        if not answer.reachable:
            if answer.path:
                return {"outcome": "invalid_submission", "feedback": "A no answer cannot include a route"}
            return {"outcome": "correct" if not routes else "incorrect",
                    "feedback": "Bounded static knight reachability checked"}
        if answer.path in routes:
            return {"outcome": "correct", "feedback": "Valid static knight route"}
        return {"outcome": "incorrect", "feedback": "The route does not meet the stated geometric and occupancy rules"}
    if isinstance(specification, ChoiceExercise) and isinstance(answer, ChoiceAnswer):
        known_ids = {option.id for option in specification.options}
        if len(set(answer.option_ids)) != len(answer.option_ids) or not set(answer.option_ids) <= known_ids:
            return {"outcome": "invalid_submission", "feedback": "Unknown or duplicate choice ID"}
        if answer.displayed_order and set(answer.displayed_order) != known_ids:
            return {"outcome": "invalid_submission", "feedback": "Displayed choice order is incomplete"}
        return {"outcome": "correct" if set(answer.option_ids) == set(specification.correct_option_ids) else "incorrect",
                "feedback": "Choice assessed against authored option IDs"}
    if isinstance(specification, ExplanationExercise) and isinstance(answer, ExplanationAnswer):
        if not answer.ready:
            return {"outcome": "invalid_submission", "feedback": "Commit your response before revealing the rubric"}
        return {"outcome": "needs_self_assessment", "feedback": "Compare your answer with the authored rubric"}
    return {"outcome": "invalid_submission", "feedback": "Unsupported answer"}
