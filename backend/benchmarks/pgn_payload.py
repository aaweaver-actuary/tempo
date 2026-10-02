"""Read-only PGN/Celery payload measurements; no broker or database connection.

PYTHONPATH=backend backend/.venv/bin/python backend/benchmarks/pgn_payload.py \
  --pgn /path/to/opening.pgn --trained-color white --initial-depth 6 \
  --output test-results/performance/pgn-payload.json

Without --pgn, measure deterministic comb trees and an approximately 425 KB
branching corpus. These synthetic cases do not reproduce production delivery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import platform
from pathlib import Path
import subprocess
import sys

import chess
import chess.pgn
from kombu.serialization import dumps

from app.celery_app import celery_app
from app.pgn_import_commands import prepare_import_payload
from app.services.pgn import parse_pgn


def branching_fixture(plies: int) -> bytes:
    """Two source nodes per ply, but quadratically many expanded line moves."""
    game = chess.pgn.Game()
    game.headers["Event"] = f"Comb {plies}"
    game.comment = "Shared plan [%cal Gg1f3]"
    node = game
    board = game.board()
    repeated_moves = ("g1f3", "g8f6", "f3g1", "f6g8")
    for ply in range(plies):
        main_move = chess.Move.from_uci(repeated_moves[ply % 4])
        alternative = next(move for move in sorted(board.legal_moves, key=lambda move: move.uci()) if move != main_move)
        continuation = node.add_variation(main_move)
        node.add_variation(alternative)
        board.push(main_move)
        node = continuation
    return str(game).encode("utf-8")


def serialized_bytes(value, serializer: str) -> int:
    _, encoding, body = dumps(value, serializer=serializer)
    return len(body.encode(encoding) if isinstance(body, str) else body)


def measure_payload(raw_bytes: bytes, source_name: str, trained_color: str, initial_depth: int) -> dict:
    raw_pgn = raw_bytes.decode("utf-8-sig")
    games, lines = parse_pgn(raw_pgn)
    payload = prepare_import_payload(source_name, trained_color, initial_depth, games, lines)
    serializer = celery_app.conf.task_serializer
    payload_bytes = serialized_bytes(payload, serializer)
    message = celery_app.amqp.create_task_message(
        task_id="pgn-payload-diagnostic", name="app.tasks.execute_foreground_command",
        args=["pgn-payload-diagnostic", "imports.pgn.admit", payload],
    )
    source_nodes = 0
    stream = io.StringIO(raw_pgn)
    while game := chess.pgn.read_game(stream):
        unvisited = list(game.variations)
        while unvisited:
            node = unvisited.pop()
            source_nodes += 1
            unvisited.extend(node.variations)
    return {
        "source_name": source_name,
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "trained_color": trained_color, "initial_depth": initial_depth,
        "raw_pgn_bytes": len(raw_bytes), "games": games,
        "source_move_nodes": source_nodes,
        "expanded_leaf_lines": len(lines),
        "total_expanded_moves": sum(len(line.moves) for line in lines),
        "annotation_occurrences": sum(len(line.annotations) for line in lines),
        "unique_segment_ids": len(payload["segment_ids"]),
        "unique_prefix_segment_ids": len(payload["prefix_segment_ids"]),
        "serializer": serializer,
        "prepared_payload_bytes": payload_bytes,
        "celery_task_body_bytes": serialized_bytes(message.body, serializer),
        "serialized_raw_ratio": payload_bytes / len(raw_bytes) if raw_bytes else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", type=Path, action="append", help="Measure a file; repeat for multiple files")
    parser.add_argument("--trained-color", choices=("white", "black"), default="white")
    parser.add_argument("--initial-depth", type=int, choices=range(2, 21), default=6)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    sources = [(path.name, path.read_bytes()) for path in options.pgn or []]
    if not sources:
        sources = [(f"comb-{plies}.pgn", branching_fixture(plies)) for plies in (16, 32, 64, 128)]
        # Existing deterministic fixture: 32-ply games, variation every five plies.
        from benchmarks.pgn_parse import fixture
        sources.append(("branching-corpus-425kb.pgn", fixture(615).encode("utf-8")))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    report = {
        "schema_version": 1, "revision": revision, "dirty_tree": dirty,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "measurement": "Configured serializer bytes; Celery task body excludes broker envelope/base64 framing. No delivery benchmark.",
        "cases": [measure_payload(raw, name, options.trained_color, options.initial_depth) for name, raw in sources],
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
