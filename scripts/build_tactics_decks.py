#!/usr/bin/env python3
"""Build deterministic, validated tactic packs from the CC0 Lichess export."""

import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import chess


MOTIFS = (
    "hangingPiece", "fork", "pin", "skewer", "discoveredAttack",
    "mateIn1", "mateIn2", "mateIn3", "mateIn4Plus",
    "calculation2", "calculation3", "calculation4", "trappedPiece",
)
RANGES = {"easy": (700, 1100), "medium": (1101, 1500), "hard": (1501, 2000)}


def motif_matches(motif: str, themes: set[str], player_moves: int) -> bool:
    if motif == "mateIn4Plus":
        return bool({"mateIn4", "mateIn5"} & themes)
    if motif.startswith("calculation"):
        return not any(theme.startswith("mate") for theme in themes) and player_moves == int(motif[-1])
    return motif in themes


def valid(row: dict[str, str]) -> bool:
    try:
        board = chess.Board(row["FEN"])
        moves = row["Moves"].split()
        if len(moves) < 2:
            return False
        for uci in moves:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                return False
            board.push(move)
        return True
    except ValueError:
        return False


def build(source: Path, destination: Path) -> None:
    targets = {f"{motif}-{stage}": (250 if stage == "focused" else 100) for motif in MOTIFS for stage in (*RANGES, "focused")}
    decks: dict[str, list[dict[str, object]]] = {deck: [] for deck in targets}
    used: set[str] = set()
    process = subprocess.Popen(["zstd", "-dc", str(source)], stdout=subprocess.PIPE)
    assert process.stdout is not None
    with io.TextIOWrapper(process.stdout, encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            puzzle_id = row["PuzzleId"]
            if puzzle_id in used:
                continue
            rating = int(row["Rating"])
            themes = set(row["Themes"].split())
            player_moves = (len(row["Moves"].split()) - 1 + 1) // 2
            candidates: list[str] = []
            for motif in MOTIFS:
                if not motif_matches(motif, themes, player_moves):
                    continue
                for stage, (minimum, maximum) in RANGES.items():
                    if minimum <= rating <= maximum:
                        candidates.append(f"{motif}-{stage}")
                if 1250 <= rating <= 2000:
                    candidates.append(f"{motif}-focused")
            deck_id = next((candidate for candidate in candidates if len(decks[candidate]) < targets[candidate]), None)
            if not deck_id or not valid(row):
                continue
            used.add(puzzle_id)
            decks[deck_id].append({
                "DeckId": deck_id,
                "Motif": deck_id.rsplit("-", 1)[0],
                "Difficulty": deck_id.rsplit("-", 1)[1],
                "DeckPosition": len(decks[deck_id]) + 1,
                "PuzzleId": puzzle_id,
                "FEN": row["FEN"],
                "Moves": row["Moves"],
                "Rating": rating,
                "RatingDeviation": int(row["RatingDeviation"]),
                "Popularity": int(row["Popularity"]),
                "NbPlays": int(row["NbPlays"]),
                "Themes": sorted(themes),
                "GameUrl": row.get("GameUrl", ""),
            })
            if all(len(decks[key]) == size for key, size in targets.items()):
                break
    process.wait()
    incomplete = {key: f"{len(decks[key])}/{size}" for key, size in targets.items() if len(decks[key]) != size}
    if incomplete:
        raise RuntimeError(f"Incomplete decks: {incomplete}")
    records = [record for deck_id in sorted(decks) for record in decks[deck_id]]
    destination.write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(records)} globally deduplicated puzzles to {destination}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_tactics_decks.py SOURCE.csv.zst DESTINATION.json")
    build(Path(sys.argv[1]), Path(sys.argv[2]))
