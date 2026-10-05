"""Offline chess/corpus functions; no database, engine, or application startup work."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import re
from typing import Iterable, TextIO
import uuid

import chess
import chess.pgn

from ..study_contracts import MoveExercise
from .study_pgn import preview_pgn
from .study_portable import TABLES, validate_bundle


GENERATOR_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/aaweaver-actuary/tempo/stalemate-swindles/v1")
MAX_RECORD_BYTES = 4 * 1024 * 1024
MATERIAL_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
MATERIAL_CHAPTERS = {"lone_king": "Lone king", "pawns_only": "Pawns only", "trapped_piece": "Piece only", "mixed_residue": "Mixed material"}
SPEED_ORDER = {"classical": 0, "rapid": 1, "blitz": 2, "bullet": 3}
HEADER_PATTERN = re.compile(r'^\[([A-Za-z0-9_]+)\s+"((?:[^"\\]|\\.)*)"\]\s*$')
MONTH_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
HISTORICAL_LIMITATION = "Historical successful swindle; this exercise does not claim that the source-game move was the engine-best move."
COUNT_NAMES = ("games_scanned", "draw_games_prefiltered", "draw_games_parsed", "terminal_stalemates", "excluded_bot", "excluded_speed", "excluded_rating", "excluded_termination", "excluded_variant", "excluded_material_deficit", "excluded_forced_single_move", "eligible_candidates", "parse_errors")


class _ResultBoardBuilder(chess.pgn.BoardBuilder):
    termination_result: str | None = None

    def visit_result(self, result: str) -> None:
        self.termination_result = result


@dataclass(frozen=True)
class MiningFilters:
    min_material_deficit: int = 5
    min_rating: int = 1000
    speeds: tuple[str, ...] = ("blitz", "rapid", "classical")
    include_bots: bool = False

    def __post_init__(self):
        if self.min_material_deficit < 0 or self.min_rating < 0 or not self.speeds or set(self.speeds) - set(SPEED_ORDER):
            raise ValueError("Use nonnegative thresholds and supported speeds: bullet,blitz,rapid,classical")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def json_bytes(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def validate_month(source_month: str) -> None:
    if not MONTH_PATTERN.fullmatch(source_month):
        raise ValueError("source-month must be YYYY-MM")
    datetime.strptime(source_month, "%Y-%m")


def new_counts() -> Counter:
    return Counter(dict.fromkeys(COUNT_NAMES, 0))


def header_rejection(headers: dict[str, str], filters: MiningFilters) -> str | None:
    if headers.get("Result") != "1/2-1/2":
        return "non_draw"
    if headers.get("Variant", "Standard") != "Standard":
        return "excluded_variant"
    if headers.get("Termination", "Normal").lower() != "normal":
        return "excluded_termination"
    if not filters.include_bots and any(headers.get(f"{color}Title", "").upper() == "BOT" for color in ("White", "Black")):
        return "excluded_bot"
    if speed_from_headers(headers) not in filters.speeds:
        return "excluded_speed"
    try:
        if min(int(headers["WhiteElo"]), int(headers["BlackElo"])) < filters.min_rating:
            return "excluded_rating"
    except (KeyError, ValueError):
        return "excluded_rating"
    return None


def speed_from_headers(headers: dict[str, str]) -> str:
    event_words = headers.get("Event", "").lower().split()
    return next((speed for speed in SPEED_ORDER if speed in event_words), "unknown")


def iter_game_blocks(stream: TextIO, filters: MiningFilters, *, max_record_bytes: int = MAX_RECORD_BYTES):
    """Retain one eligible draw only; reject headers before retaining its movetext.

    Lichess starts every record with a header block. Track multiline brace
    comments so header-shaped text inside comments cannot start a game.
    Oversized records resynchronize at the next Event header.
    """
    headers: dict[str, str] = {}
    retained_lines: list[str] = []
    in_movetext = False
    rejection = None
    malformed = False
    record_bytes = 0
    comment_depth = 0
    while True:
        source_line = stream.readline(max_record_bytes + 1)
        if not source_line:
            break
        oversized_line = len(source_line.encode("utf-8")) > max_record_bytes
        if oversized_line and not source_line.endswith("\n"):
            while source_line and not source_line.endswith("\n"):
                source_line = stream.readline(max_record_bytes + 1)
            source_line = "\n"
        stripped_line = source_line.strip()
        starts_header = stripped_line.startswith("[") and (comment_depth == 0 or malformed and stripped_line.startswith('[Event "'))
        if starts_header and (in_movetext or headers and stripped_line.startswith('[Event "')):
            yield headers, "".join(retained_lines), rejection, malformed
            headers, retained_lines = {}, []
            in_movetext, malformed, record_bytes, comment_depth = False, False, 0, 0
        if oversized_line:
            malformed = True
            retained_lines.clear()
            in_movetext = True
            rejection = header_rejection(headers, filters)
        if starts_header and not in_movetext:
            header_match = HEADER_PATTERN.fullmatch(stripped_line)
            if header_match:
                header_name, header_value = header_match.groups()
                if header_name in headers:
                    malformed = True
                headers[header_name] = header_value.replace('\\"', '"').replace("\\\\", "\\")
            else:
                malformed = True
            retained_lines.append(source_line)
            record_bytes += len(source_line.encode("utf-8"))
        elif not stripped_line and not in_movetext:
            if headers and retained_lines and retained_lines[-1].strip():
                retained_lines.append("\n")
            continue
        else:
            if not headers and not malformed:
                if not stripped_line:
                    continue
                malformed = True
            if not in_movetext:
                in_movetext = True
                rejection = header_rejection(headers, filters)
                if rejection:
                    retained_lines.clear()
            if rejection is None and not malformed:
                retained_lines.append(source_line)
                record_bytes += len(source_line.encode("utf-8"))
            # PGN semicolon comments end at newline; brace comments may span it.
            comment_text = source_line.split(";", 1)[0]
            comment_depth = max(0, comment_depth + comment_text.count("{") - comment_text.count("}"))
        if oversized_line or record_bytes > max_record_bytes:
            malformed = True
            retained_lines.clear()
    if headers or in_movetext or malformed:
        yield headers, "".join(retained_lines), header_rejection(headers, filters), malformed or comment_depth != 0


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(value * len(board.pieces(piece_type, color)) for piece_type, value in MATERIAL_VALUES.items())


def pattern_features(board: chess.Board, defender: chess.Color, swindle_kind: str) -> dict:
    has_pawns = bool(board.pieces(chess.PAWN, defender))
    has_pieces = any(board.pieces(piece_type, defender) for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN))
    material_class = "mixed_residue" if has_pawns and has_pieces else "pawns_only" if has_pawns else "trapped_piece" if has_pieces else "lone_king"
    king_square = board.king(defender)
    if king_square is None:
        raise ValueError("Defender king missing")
    file_index, rank_index = chess.square_file(king_square), chess.square_rank(king_square)
    king_zone = "corner" if file_index in (0, 7) and rank_index in (0, 7) else "edge" if file_index in (0, 7) or rank_index in (0, 7) else "interior"
    def bucket(piece_type):
        count = len(board.pieces(piece_type, not defender))
        return str(count) if count < 2 else "2plus"
    return {"final_material_class": material_class, "king_zone": king_zone,
            "motif_signature": f"stalemate:{material_class}:{king_zone}:q{bucket(chess.QUEEN)}:r{bucket(chess.ROOK)}:{swindle_kind}"}


def candidate_key(puzzle_fen: str, swindle_move_uci: str) -> str:
    canonical_fen = " ".join(chess.Board(puzzle_fen).fen().split()[:4])
    return hashlib.sha256(f"{canonical_fen}\n{swindle_move_uci}".encode()).hexdigest()


def extract_candidate(headers: dict[str, str], terminal_board: chess.Board, source_month: str,
                      filters: MiningFilters, counts: Counter) -> dict | None:
    if not terminal_board.is_valid():
        raise ValueError("Invalid reconstructed chess position")
    if not terminal_board.is_stalemate():
        return None
    counts["terminal_stalemates"] += 1
    if len(terminal_board.move_stack) < 2:
        return None
    terminal_fen = terminal_board.fen()
    defender = terminal_board.turn
    puzzle_board = terminal_board.copy(stack=True)
    opponent_reply = puzzle_board.pop()
    opponent_reply_san = puzzle_board.san(opponent_reply)
    swindle_move = puzzle_board.pop()
    if puzzle_board.turn != defender or swindle_move not in puzzle_board.legal_moves:
        raise ValueError("Source continuation does not start with the defender")
    legal_move_count = puzzle_board.legal_moves.count()
    if legal_move_count < 2:
        counts["excluded_forced_single_move"] += 1
        return None
    defender_material = material(puzzle_board, defender)
    attacker_material = material(puzzle_board, not defender)
    material_deficit = attacker_material - defender_material
    if material_deficit < filters.min_material_deficit:
        counts["excluded_material_deficit"] += 1
        return None
    puzzle_fen = puzzle_board.fen()
    swindle_move_san = puzzle_board.san(swindle_move)
    puzzle_ply = puzzle_board.ply()
    fullmove_number = puzzle_board.fullmove_number
    puzzle_board.push(swindle_move)
    if opponent_reply not in puzzle_board.legal_moves:
        raise ValueError("Illegal historical opponent reply")
    opponent_replies = list(puzzle_board.legal_moves)
    stalemating_replies = []
    for reply in opponent_replies:
        puzzle_board.push(reply)
        if puzzle_board.is_stalemate():
            stalemating_replies.append(reply.uci())
        puzzle_board.pop()
    if opponent_reply.uci() not in stalemating_replies:
        raise ValueError("Historical reply does not produce stalemate")
    swindle_kind = "immediate_forced_stalemate" if len(stalemating_replies) == len(opponent_replies) else "historical_trap"
    source_url = headers.get("Site", "")
    game_match = re.fullmatch(r"https://lichess\.org/([A-Za-z0-9]{8})(?:/(?:white|black))?", source_url)
    if not game_match:
        raise ValueError("Source must identify a Lichess game URL")
    ratings = {color: int(headers[f"{color}Elo"]) for color in ("White", "Black")}
    defender_name, attacker_name = ("White", "Black") if defender else ("Black", "White")
    candidate = {
        "schema_version": 1, "candidate_key": candidate_key(puzzle_fen, swindle_move.uci()),
        "game_id": game_match.group(1), "game_url": f"https://lichess.org/{game_match.group(1)}",
        "source_month": source_month, "date": headers.get("UTCDate", headers.get("Date", "????.??.??")),
        "event": headers.get("Event", ""), "time_control": headers.get("TimeControl", "?"), "speed": speed_from_headers(headers),
        "white_rating": ratings["White"], "black_rating": ratings["Black"],
        "is_bot": any(headers.get(f"{color}Title", "").upper() == "BOT" for color in ("White", "Black")),
        "defender_color": defender_name.lower(), "attacker_color": attacker_name.lower(),
        "defender_rating": ratings[defender_name], "attacker_rating": ratings[attacker_name],
        "puzzle_fen": puzzle_fen, "canonical_puzzle_fen": " ".join(puzzle_fen.split()[:4]), "terminal_fen": terminal_fen,
        "swindle_move_uci": swindle_move.uci(), "swindle_move_san": swindle_move_san,
        "opponent_reply_uci": opponent_reply.uci(), "opponent_reply_san": opponent_reply_san,
        "puzzle_ply": puzzle_ply, "fullmove_number": fullmove_number,
        "defender_material": defender_material, "attacker_material": attacker_material, "material_deficit": material_deficit,
        "defender_legal_move_count": legal_move_count, "opponent_reply_count": len(opponent_replies),
        "stalemating_reply_count": len(stalemating_replies), "stalemating_replies_uci": sorted(stalemating_replies), "swindle_kind": swindle_kind,
        **pattern_features(terminal_board, defender, swindle_kind),
    }
    counts["eligible_candidates"] += 1
    return candidate


def mine_candidates(stream: TextIO, source_month: str, filters: MiningFilters, counts: Counter,
                    *, strict: bool = False, max_games: int | None = None,
                    stop_after_candidates: int | None = None, progress_every: int = 1_000_000,
                    progress=None, max_record_bytes: int = MAX_RECORD_BYTES):
    validate_month(source_month)
    for headers, raw_pgn, rejection, malformed in iter_game_blocks(stream, filters, max_record_bytes=max_record_bytes):
        if max_games is not None and counts["games_scanned"] >= max_games:
            break
        counts["games_scanned"] += 1
        try:
            if malformed:
                raise ValueError("Malformed or oversized PGN record")
            if headers.get("Result") == "1/2-1/2":
                counts["draw_games_prefiltered"] += 1
            if rejection:
                if rejection != "non_draw":
                    counts[rejection] += 1
            else:
                replay_visitor = _ResultBoardBuilder()
                final_board = chess.pgn.read_game(io.StringIO(raw_pgn), Visitor=lambda: replay_visitor)
                if final_board is None:
                    raise ValueError("No game parsed")
                # The movetext result must agree with the draw header.
                if replay_visitor.termination_result != "1/2-1/2":
                    raise ValueError("Movetext result disagrees with draw header")
                counts["draw_games_parsed"] += 1
                candidate = extract_candidate(headers, final_board, source_month, filters, counts)
                if candidate:
                    yield candidate
                    if stop_after_candidates is not None and counts["eligible_candidates"] >= stop_after_candidates:
                        break
        except (ValueError, AssertionError, KeyError) as error:
            counts["parse_errors"] += 1
            if strict:
                raise ValueError(f"Malformed game {headers.get('Site', 'unknown')}: {error}") from error
        if progress and counts["games_scanned"] % progress_every == 0:
            progress(dict(counts))


def candidate_rank(candidate: dict) -> tuple:
    return (bool(candidate["is_bot"]), -candidate["material_deficit"],
            -min(candidate["defender_rating"], candidate["attacker_rating"]),
            SPEED_ORDER[candidate["speed"]], candidate["game_id"], canonical_json(candidate))


def verify_candidate(candidate: dict) -> None:
    """Recompute chess facts at the JSONL boundary rather than trusting annotations."""
    if candidate.get("schema_version") != 1:
        raise ValueError("Unsupported candidate schema version")
    validate_month(candidate["source_month"])
    root_board = chess.Board(candidate["puzzle_fen"])
    if not root_board.is_valid():
        raise ValueError("Invalid puzzle position")
    for move_text in (candidate["swindle_move_uci"], candidate["opponent_reply_uci"]):
        move = chess.Move.from_uci(move_text)
        if move not in root_board.legal_moves:
            raise ValueError("Illegal candidate continuation")
        root_board.push(move)
    headers = {"Result": "1/2-1/2", "Site": candidate["game_url"], "Event": candidate["event"],
               "Date": candidate["date"], "TimeControl": candidate["time_control"],
               "WhiteElo": str(candidate["white_rating"]), "BlackElo": str(candidate["black_rating"])}
    if candidate["is_bot"]:
        headers["WhiteTitle"] = "BOT"
    verified = extract_candidate(headers, root_board, candidate["source_month"], MiningFilters(0, 0, tuple(SPEED_ORDER), True), new_counts())
    if verified is None or verified != candidate:
        raise ValueError("Candidate metadata disagrees with reconstructed chess facts")


def select_candidates(candidates: Iterable[dict], *, max_puzzles: int = 300, max_per_motif: int = 40) -> tuple[list[dict], dict]:
    if max_puzzles < 1 or max_per_motif < 1:
        raise ValueError("Corpus and motif limits must be positive")
    representatives: dict[str, dict] = {}
    for candidate in candidates:
        verify_candidate(candidate)
        key = candidate["candidate_key"]
        existing = representatives.get(key)
        if existing is None or candidate_rank(candidate) < candidate_rank(existing):
            representatives[key] = candidate
    buckets = defaultdict(list)
    for candidate in representatives.values():
        buckets[candidate["motif_signature"]].append(candidate)
    ranked_buckets = [sorted(buckets[motif], key=candidate_rank)[:max_per_motif] for motif in sorted(buckets)]
    selected = []
    for round_index in range(max_per_motif):
        for bucket in ranked_buckets:
            if round_index < len(bucket) and len(selected) < max_puzzles:
                selected.append(bucket[round_index])
        if len(selected) >= max_puzzles:
            break
    unique_count = len(representatives)
    capacity = sum(len(bucket) for bucket in ranked_buckets)
    return selected, {"unique_candidates": unique_count, "selected_puzzles": len(selected),
                      "shortfall_insufficient_candidates": max(0, max_puzzles - unique_count),
                      "shortfall_motif_cap": max(0, min(max_puzzles, unique_count) - capacity)}


def mini_pgn(candidate: dict) -> str:
    game = chess.pgn.Game()
    game.setup(chess.Board(candidate["puzzle_fen"]))
    game.headers.update({"Event": "Stalemate Swindles", "White": "White", "Black": "Black",
                         "Site": candidate["game_url"], "Date": candidate["date"], "UTCDate": candidate["date"],
                         "TimeControl": candidate["time_control"], "WhiteElo": str(candidate["white_rating"]),
                         "BlackElo": str(candidate["black_rating"]), "Result": "1/2-1/2"})
    game.add_variation(chess.Move.from_uci(candidate["swindle_move_uci"])).add_variation(chess.Move.from_uci(candidate["opponent_reply_uci"]))
    return game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False)) + "\n"


def exercise_specification(candidate: dict) -> MoveExercise:
    forced = candidate["swindle_kind"] == "immediate_forced_stalemate"
    further_analysis = f"{HISTORICAL_LIMITATION} Source: {candidate['game_url']}. Material deficit: {candidate['material_deficit']} points. Pattern: {candidate['final_material_class']}, {candidate['king_zone']} king."
    if forced:
        further_analysis += f" After {candidate['swindle_move_san']}, all {candidate['opponent_reply_count']} legal opponent replies immediately produce stalemate. This verifies only the next-ply property."
    return MoveExercise(type="move_line", grading_policy="open_judgment", mode="single",
        accepted_lines=[[candidate["swindle_move_uci"]]],
        prompt="You are materially losing. Find the move that forces an immediate stalemate on the opponent's next move." if forced else "You are materially losing. Find the move that set the successful stalemate trap in this game.",
        hint="Look for a move that restricts your future mobility or makes your remaining material disposable.",
        explanation=f"{candidate['swindle_move_san']} created the stalemate opportunity. In the source game, {candidate['opponent_reply_san']} followed and the resulting position was stalemate.",
        further_analysis=further_analysis)


def build_bundle(selected: list[dict], corpus_id: str, title: str = "Stalemate Swindles") -> dict:
    if not selected:
        raise ValueError("No qualifying puzzles selected")
    months = {candidate["source_month"] for candidate in selected}
    if len(months) != 1:
        raise ValueError("A corpus revision must have one source month")
    source_month = next(iter(months))
    timestamp = datetime.strptime(source_month, "%Y-%m").replace(tzinfo=timezone.utc).isoformat()
    def stable_id(role: str, identity: str = "") -> str:
        return str(uuid.uuid5(GENERATOR_NAMESPACE, canonical_json([corpus_id, role, identity])))
    study_id = stable_id("study")
    tables = {name: [] for name in TABLES}
    tables["studies"].append({"id": study_id, "title": title,
        "description": "Historical stalemate escapes mined from Lichess rated games. Each exercise asks for the losing side's move that created a successful stalemate opportunity in the source game. Historical-trap examples are not claims of objectively best play.",
        "source_json": canonical_json({"generator": "stalemate_swindles_v1", "provider": "lichess", "source_month": source_month, "license": "CC0", "corpus_id": corpus_id}),
        "archived": 0, "created_at": timestamp, "updated_at": timestamp})
    for chapter_index, (material_class, chapter_title) in enumerate(MATERIAL_CHAPTERS.items()):
        tables["study_chapters"].append({"id": stable_id("chapter", material_class), "study_id": study_id,
            "title": chapter_title, "description": "", "position": chapter_index, "archived": 0})
    for candidate in selected:
        verify_candidate(candidate)
        key = candidate["candidate_key"]
        source_id = stable_id("source", key)
        raw_pgn = mini_pgn(candidate)
        preview = preview_pgn(raw_pgn)
        record = preview["records"][0]
        if not record["valid"] or len(record["nodes"]) != 3:
            raise ValueError("Generated source must have exactly one valid two-ply continuation")
        tables["study_sources"].append({"id": source_id, "chapter_id": stable_id("chapter", candidate["final_material_class"]),
            "source_group_id": stable_id("source_group", key), "version": 1, "raw_pgn": record["raw_pgn"], "sha256": preview["digest"],
            "filename": f"stalemate-{candidate['game_id']}.pgn", "record_index": 0,
            "headers_json": canonical_json(record["headers"]), "diagnostics_json": "[]", "valid": 1, "created_at": timestamp})
        for node in record["nodes"]:
            tables["study_positions"].append({"id": stable_id("position", f"{key}:{node['path']}"), "source_id": source_id,
                "parent_id": stable_id("position", f"{key}:{node['parent_path']}") if node["parent_path"] else None,
                "child_index": node["child_index"], "move_uci": node["move_uci"], "fen": node["fen"], "history_json": canonical_json(node["history"]),
                "comment": node["comment"], "starting_comment": node["starting_comment"], "nags_json": canonical_json(node["nags"]),
                "arrows_json": canonical_json(node["arrows"]), "squares_json": canonical_json(node["squares"]), "node_path": node["path"], "valid": 1})
        exercise_id = stable_id("exercise", key)
        metadata = {field: str(value) for field, value in candidate.items() if field not in {"stalemating_replies_uci", "schema_version", "is_bot"}}
        metadata.update({"kind": "stalemate_swindle_v1", "provider": "lichess", "corpus_id": corpus_id})
        tables["study_exercises"].append({"id": exercise_id, "study_id": study_id,
            "position_id": stable_id("position", f"{key}:root"), "current_revision": 1, "status": "draft", "sibling_group": candidate["motif_signature"],
            "source_json": canonical_json(metadata), "point_value": None, "created_at": timestamp, "updated_at": timestamp})
        specification_json = canonical_json(exercise_specification(candidate).model_dump(mode="json"))
        tables["study_exercise_revisions"].append({"exercise_id": exercise_id, "revision": 1,
            "specification_json": specification_json, "specification_hash": hashlib.sha256(specification_json.encode()).hexdigest(), "created_at": timestamp})
    bundle = {"format": "tempo-study", "schema_version": 1, "tables": tables, "excludes_review_history": True}
    validate_bundle(bundle)
    return bundle


def selected_distribution(selected: list[dict]) -> dict:
    distribution = {field: dict(sorted(Counter(candidate[field] for candidate in selected).items()))
                    for field in ("final_material_class", "king_zone", "swindle_kind", "speed")}
    distribution["minimum_rating_bucket"] = dict(sorted(Counter(f"{min(candidate['defender_rating'], candidate['attacker_rating']) // 500 * 500}-{min(candidate['defender_rating'], candidate['attacker_rating']) // 500 * 500 + 499}" for candidate in selected).items()))
    distribution["material_deficit_bucket"] = dict(sorted(Counter("0-4" if candidate["material_deficit"] < 5 else "5-9" if candidate["material_deficit"] < 10 else "10-19" if candidate["material_deficit"] < 20 else "20plus" for candidate in selected).items()))
    return distribution
