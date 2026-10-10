"""Explicit read-only capture and database-independent JSON shadow comparison."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.opening_ranking_evaluation import (
    OpeningRankingError, ProductionOrderScorer, ShorterCardTestScorer,
    canonical_json, compare, load_snapshot, rank,
)

SCORERS = {"production-order": ProductionOrderScorer, "shorter-card-test": ShorterCardTestScorer}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Capture current PostgreSQL candidates; never admit cards")
    capture.add_argument("--study-day", help="Optional current server-local production day; backdating is rejected")
    capture.add_argument("--repertoire-id", action="append", default=[])
    capture.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare", help="Replay a saved snapshot without database access")
    comparison.add_argument("--snapshot", type=Path, required=True)
    comparison.add_argument("--scorer", choices=SCORERS, action="append")
    comparison.add_argument("--top-k", type=int, action="append")
    comparison.add_argument("--output", type=Path)
    options = parser.parse_args(arguments)
    try:
        if options.command == "capture":
            from app.services.opening_ranking_snapshot import capture_snapshot
            result = capture_snapshot(study_day=options.study_day, repertoire_ids=options.repertoire_id)
        else:
            # Limit input before parsing; oversized files cannot bypass capture budgets.
            with options.snapshot.open("rb") as source:
                raw = source.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise OpeningRankingError("limit_exceeded", "Snapshot file exceeds 4 MiB.")
            document = json.loads(raw)
            candidates, context = load_snapshot(document)
            scorers = [SCORERS[name]() for name in (options.scorer or list(SCORERS))]
            result = compare(candidates, [rank(candidates, context, scorer) for scorer in scorers],
                             options.top_k if options.top_k is not None else (1, 5, 10))
            result.update({"snapshot_id": context.snapshot_id, "as_of": context.as_of, "study_day": context.study_day,
                           "production_order_basis": document["production_order_basis"],
                           "actual_admission_plan": document["actual_admission_plan"],
                           "actual_admission_plan_basis": document["actual_admission_plan_basis"],
                           "source_versions": document["source_versions"],
                           "historical_counterfactual_supported": False, "limitations": document["limitations"]})
        rendered = canonical_json(result) + "\n"
        if options.output:
            # Exclusive creation protects existing diagnostic evidence and input snapshots.
            with options.output.open("x", encoding="utf-8") as destination:
                destination.write(rendered)
        else:
            sys.stdout.write(rendered)
        return 0
    except OpeningRankingError as error:
        sys.stderr.write(canonical_json({"status": "error", "code": error.code, "message": str(error)}) + "\n")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        sys.stderr.write(canonical_json({"status": "error", "code": "invalid_input_or_output",
                                        "message": f"Unable to read valid snapshot or create output ({type(error).__name__}); check paths and JSON contract."}) + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
