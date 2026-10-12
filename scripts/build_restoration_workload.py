"""Public deterministic legal positions; never reads a user's repertoire or DB."""
import hashlib
import json
from pathlib import Path
import random
import sys

import chess


def build():
    random_source = random.Random(190)
    lines = []
    unique_positions = set()
    for line_index in range(8268):
        move_count = 30 if line_index < 2463 else 29
        # Independently varied legal starting positions exercise the snapshot's
        # FEN maps and transfer volume, rather than repeated knight-cycle keys.
        while True:
            board = chess.Board()
            moves = []
            positions = []
            for ply in range(12 + move_count):
                candidates = sorted(board.legal_moves, key=lambda move: move.uci())
                if not candidates:
                    break
                if ply == 12:
                    starting_fen = board.fen(en_passant="legal")
                    positions.append(" ".join(starting_fen.split()[:4]))
                move = random_source.choice(candidates)
                board.push(move)
                if ply >= 12:
                    moves.append(move.uci())
                    positions.append(" ".join(board.fen(en_passant="legal").split()[:4]))
            if len(moves) == move_count:
                break
        unique_positions.update(positions)
        lines.append(dict(id=f"public-{line_index}", repertoire_id=f"public-{line_index % 10}",
                          repertoire_name="Public deterministic", name=f"Line {line_index}",
                          trained_color="white", start_fen=starting_fen, moves=moves))
    payload = dict(lines=lines)
    encoded = json.dumps(payload, separators=(",", ":")) + "\n"
    target = Path(sys.argv[1])
    target.write_text(encoded)
    metadata = dict(generator="build_restoration_workload.py", seed=190, python_chess=chess.__version__,
                    lines=len(lines), indexed_positions=sum(len(line["moves"]) + 1 for line in lines),
                    unique_fen_keys=len(unique_positions), bytes=len(encoded.encode()),
                    sha256=hashlib.sha256(encoded.encode()).hexdigest())
    target.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    build()
