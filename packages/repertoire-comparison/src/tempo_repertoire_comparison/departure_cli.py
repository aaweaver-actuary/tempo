"""Minimal file-to-stdout first-departure adapter."""
import argparse
import json
from pathlib import Path
from tempo_game_review_contracts import CanonicalGame, RepertoireSnapshot
from .cli import read_value
from .departure import first_departure


def main(argv=None):
    parser = argparse.ArgumentParser(description="Return the first departure as a one-based ply, or null when fully covered")
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = first_departure(read_value(arguments.game, CanonicalGame), read_value(arguments.snapshot, RepertoireSnapshot))
    except (OSError, ValueError) as error:
        parser.exit(2, f"Unsupported comparison input: {error}\n")
    print(json.dumps(result["ply"] if result["status"] == "compared" else result))
    return 0 if result["status"] == "compared" else 2
