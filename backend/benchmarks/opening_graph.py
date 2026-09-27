"""Deterministic opening graph timing; run through scripts/benchmark-graph.mjs."""

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

from app.services.opening_graph import GraphInput, build_graph


def graph_input(line_count: int) -> GraphInput:
    lines = []
    for line_index in range(line_count):
        board = chess.Board()
        moves = []
        for ply_index in range(16):
            legal_moves = list(board.legal_moves)
            move = legal_moves[(line_index + ply_index) % len(legal_moves)]
            moves.append(move.uci())
            board.push(move)
            if board.is_game_over():
                break
        lines.append({
            "id": f"line-{line_index}",
            "start_fen": chess.STARTING_FEN,
            "moves_json": json.dumps(moves),
            "trained_color": "white" if line_index % 2 == 0 else "black",
            "learner_decision_count": 3,
        })
    return GraphInput("graph-benchmark", tuple(lines), 3)


def main() -> None:
    cases = []
    for name, line_count in (("small", 10), ("typical", 100), ("large", 500), ("stress", 2_000)):
        fixture = graph_input(line_count)
        build_graph(fixture)
        samples_ms = []
        for _ in range(7):
            started_at = time.perf_counter()
            steps = build_graph(fixture)
            samples_ms.append((time.perf_counter() - started_at) * 1000)
        cases.append({
            "name": name,
            "lines": line_count,
            "source_plies": sum(len(json.loads(line["moves_json"])) for line in fixture.lines),
            "graph_steps": len(steps),
            "samples_ms": samples_ms,
            "p50_ms": statistics.median(samples_ms),
            "p95_ms": max(samples_ms),
        })
    report = {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fixture": "deterministic-16-ply-three-decision-v1",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "cases": cases,
    }
    output_path = Path("test-results/performance/opening-graph-benchmark.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({case["name"]: round(case["p50_ms"], 1) for case in cases}))
    print(output_path)


if __name__ == "__main__":
    main()
