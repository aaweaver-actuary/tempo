"""Explicit bounded file-in/file-out adapter. No providers, connections or actions."""
import argparse
from pathlib import Path

from tempo_game_review_contracts import CanonicalGame, RepertoireSnapshot
from .comparison import compare

MAX_INPUT_BYTES = 8 * 1024 * 1024


def read_value(path, model):
    with path.open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("comparison_input_exceeds_8_mib")
    return model.model_validate_json(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare one supplied canonical game and captured repertoire snapshot offline")
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        report = compare(read_value(arguments.game, CanonicalGame), read_value(arguments.snapshot, RepertoireSnapshot))
        with arguments.output.open("x", encoding="utf-8") as output:
            output.write(report.model_dump_json(indent=2) + "\n")
    except (OSError, ValueError) as error:
        parser.exit(2, f"Comparison failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
