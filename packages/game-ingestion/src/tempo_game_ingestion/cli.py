"""Explicit local file adapter. JSON goes to stdout; no implicit downstream work."""
import argparse
from pathlib import Path
import sys

from . import FileInput, PgnGameIngester


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Normalize one completed PGN to a canonical game.")
    parser.add_argument("input", type=Path)
    for name in ("workspace-id", "game-id", "source-id", "player"):
        parser.add_argument(f"--{name}", required=True)
    for name in ("game-revision", "source-revision"):
        parser.add_argument(f"--{name}", required=True, type=int)
    parser.add_argument("--played-at")
    options = vars(parser.parse_args(arguments))
    source = FileInput(options.pop("input"))
    try:
        game = PgnGameIngester().ingest(source, **options)
    except (OSError, ValueError) as error:
        print(f"Game ingestion failed: {error}", file=sys.stderr)
        return 1
    print(game.model_dump_json())
    return 0
