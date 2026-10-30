"""Explicit managed Games mode read/record command; importing does no work.

The managed operator owns verification and the target lock. Record runs through
the frozen optional service's existing writer profile, never the read-only API
profile. This command neither starts a worker nor touches provider/Study data.
"""
from __future__ import annotations

import argparse
import json
import os


def main(argv=None, *, reader_factory=None, writer_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("status", "record"))
    parser.add_argument("--workspace", default="legacy")
    parser.add_argument("--intent-json")
    parser.add_argument("--expected-generation", type=int)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args(argv)
    if not 1 <= len(options.workspace) <= 200:
        parser.error("workspace must be 1..200 characters")
    from tempo_legacy_database.adapters.games.managed_mode import ManagedGamesModeStore, read_feedback_mode
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
    if options.operation == "status":
        if options.apply or options.intent_json is not None or options.expected_generation is not None:
            parser.error("status accepts no mutation arguments")
        if reader_factory is None:
            dsn = os.environ.get("TEMPO_DATABASE_READ_URL")
            if not dsn:
                raise ValueError("managed_mode_reader_profile_required")
            connections = DedicatedGamesConnections(dsn, read_only=True)
        else:
            connections = reader_factory()
        intent = read_feedback_mode(connections, options.workspace)
        print(json.dumps({"schema_version": 1, "read_only": True,
                          "intent": intent.model_dump(mode="json") if intent else None}))
        return 0
    if not options.apply or options.intent_json is None or options.expected_generation is None or options.expected_generation < 0:
        parser.error("record requires --apply, --intent-json and nonnegative --expected-generation")
    if len(options.intent_json.encode()) > 16 * 1024:
        parser.error("managed intent exceeds its finite bound")
    from app.games.feedback.interfaces.managed_mode import ManagedGamesModeIntent
    intent = ManagedGamesModeIntent.model_validate_json(options.intent_json)
    if intent.workspace_id != options.workspace:
        parser.error("managed intent workspace mismatch")
    if writer_factory is None:
        dsn = os.environ.get("TEMPO_DATABASE_WRITE_URL")
        if not dsn:
            raise ValueError("managed_mode_writer_profile_required")
        connections = DedicatedGamesConnections(dsn)
    else:
        connections = writer_factory()
    saved = ManagedGamesModeStore(connections).record(intent, expected_generation=options.expected_generation)
    print(json.dumps({"schema_version": 1, "applied": True, "generation": saved.generation,
                      "mode": saved.mode, "phase": saved.phase, "operation_id": saved.operation_id}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Never publish raw database/connection errors or supplied receipt text.
        print(json.dumps({"schema_version": 1, "applied": False, "reason": "managed_mode_operation_failed"}))
        raise SystemExit(2)
