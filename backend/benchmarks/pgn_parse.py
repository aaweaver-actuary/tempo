"""Deterministic PGN import timings; run through scripts/benchmark-pgn.mjs."""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.pgn

from app.services.pgn import parse_pgn


def fixture(game_count: int) -> str:
    games: list[str] = []
    for game_index in range(game_count):
        game = chess.pgn.Game()
        game.headers["Event"] = f"Benchmark {game_index}"
        node = game
        board = game.board()
        for ply_index in range(32):
            legal_moves = list(board.legal_moves)
            main_move = legal_moves[(game_index + ply_index) % len(legal_moves)]
            if ply_index % 5 == 0 and len(legal_moves) > 1:
                alternative = legal_moves[(game_index + ply_index + 1) % len(legal_moves)]
                branch = node.add_variation(alternative)
                branch.comment = "Alternate plan [%cal Ge2e4]"
                branch_board = board.copy(stack=False)
                branch_board.push(alternative)
                for _ in range(2):
                    if branch_board.is_game_over():
                        break
                    reply = list(branch_board.legal_moves)[0]
                    branch = branch.add_variation(reply)
                    branch_board.push(reply)
            node = node.add_variation(main_move)
            board.push(main_move)
            if board.is_game_over():
                break
        games.append(str(game))
    return "\n\n".join(games)


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * fraction + 0.999999) - 1)]


def main() -> None:
    cases = []
    for label, game_count in (("small", 1), ("typical", 10), ("large", 40), ("stress", 100)):
        raw_pgn = fixture(game_count)
        parse_pgn(raw_pgn)
        durations_ms: list[float] = []
        for _ in range(7):
            started_at = time.perf_counter()
            parsed_games, lines = parse_pgn(raw_pgn)
            durations_ms.append((time.perf_counter() - started_at) * 1000)
        cases.append({
            "name": label,
            "games": parsed_games,
            "lines": len(lines),
            "line_plies": sum(len(line.moves) for line in lines),
            "pgn_bytes": len(raw_pgn.encode()),
            "samples_ms": durations_ms,
            "p50_ms": statistics.median(durations_ms),
            "p95_ms": percentile(durations_ms, 0.95),
        })
    report = {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fixture": "deterministic-32-ply-with-variation-every-5-plies-v1",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "cases": cases,
    }
    output_path = Path("test-results/performance/pgn-parse-benchmark.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({case["name"]: round(case["p50_ms"], 1) for case in cases}))
    print(output_path)


if __name__ == "__main__":
    main()
