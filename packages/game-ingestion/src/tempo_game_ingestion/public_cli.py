"""Explicit public Lichess window CLI; no credentials or implicit followups."""
import argparse
from datetime import datetime
import json
import sys

from .lichess import PublicImportError, UrlPublicTransport, fetch_lichess_games


def main(arguments=None, *, transport=None):
    parser = argparse.ArgumentParser(description="Fetch one bounded public Lichess game window as canonical JSON.")
    for name in ("username", "since", "until"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--workspace-id", default="public")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--game-revision", type=int, default=1)
    parser.add_argument("--source-revision", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=10)
    options = vars(parser.parse_args(arguments))
    try:
        for name in ("since", "until"):
            options[name] = datetime.fromisoformat(options[name].replace("Z", "+00:00"))
        result = fetch_lichess_games(transport if transport is not None else UrlPublicTransport(), **options)
    except (ValueError, OSError) as error:
        print(f"Public game ingestion failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"provider": "lichess", "username": result.username, "since": result.since,
        "until": result.until, "limit": result.limit, "limit_reached": result.limit_reached,
        "games": [game.model_dump(mode="json") for game in result.games]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
