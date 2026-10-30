"""Explicit read-only collection status command. Importing starts nothing."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

MAX_OUTPUT_BYTES = 256 * 1024
PRIVATE_FIELDS = frozenset({"account", "workspace_id", "provider_game_id", "canonical_game_id",
    "game_id", "job_id", "source_key", "repertoire_id", "repertoire_ids", "presentation_id",
    "decision_id", "operation_id", "owner_receipt_id", "position_key", "expected_uci", "played_uci",
    "source_digest", "comparison_revision", "name"})


def _redacted(value, key=""):
    if isinstance(value, bool):
        return value  # Presence flags are public metadata, not private identities.
    if key == "account" and value is not None:
        return "redacted"
    if key in {"diagnostic", "selection_diagnostic"} and isinstance(value, str):
        if value.startswith("comparison_unavailable:"):
            return "comparison_unavailable:[redacted]"
        safe = {"no_compatible_repertoire", "unresolved_parsing_or_comparison_boundary",
            "changed_source_requires_D08_resolution", "snapshot_incomplete_or_unavailable",
            "collection_read_unavailable", "retained_collection_comparison_absent", "ambiguous_stored_game",
            "empty_findings_are_not_comparison_completion_proof", "receipt_not_observed", "not_delivered",
            "owner_receipt_read_unavailable", "repertoire_reader_not_supplied", "repertoire_read_unavailable",
            "selected_repertoire_ids_absent", "process_and_delivery_disablement_require_runtime_evidence"}
        return value if value in safe else "redacted_diagnostic"
    if value is not None and key in PRIVATE_FIELDS:
        # Stable opaque identities preserve correspondence without publishing raw values.
        return "redacted:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]
    if isinstance(value, dict):
        return {k: _redacted(v, k) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_redacted(item) for item in value]
    return value


def _encode(report, *, private=False):
    result = report if private else _redacted(report)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > MAX_OUTPUT_BYTES:
        # Never cut JSON bytes or claim the omitted detail was fully reported.
        result = {"schema_version": 1, "read_only": True, "truncated": True,
                  "identity": result["identity"], "unresolved": ["output_exceeds_256_kib"]}
        result.update({name: {"availability": "unavailable", "diagnostic": "output_exceeds_256_kib"}
                       for name in ("providers", "collection", "delivery", "selected_game")})
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    return encoded


def main(argv=None, *, reader_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="legacy")
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--provider", choices=("lichess", "chesscom", "chess.com"))
    parser.add_argument("--game-id", help="Provider game ID; requires --provider")
    parser.add_argument("--private", action="store_true", help="Include private saved scope and game evidence; never credentials/PGNs")
    options = parser.parse_args(argv)
    if options.game_id and not options.provider:
        parser.error("--game-id requires --provider")
    if len(options.workspace) > 200 or options.game_id and len(options.game_id) > 200:
        parser.error("workspace/game ID must be at most 200 characters")
    report = {"schema_version": 1, "read_only": True, "unresolved": []}
    dsn = os.environ.get("TEMPO_DATABASE_READ_URL")
    if not dsn and reader_factory is None:
        report.update({name: {"availability": "unavailable"} for name in
                       ("identity", "providers", "collection", "delivery")})
        report["selected_game"] = None
        report["unresolved"].append("games_reader_profile_required")
    else:
        if reader_factory is None:
            from tempo_legacy_database.adapters.games.collection_observations import CollectionObservations
            from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
            connections = DedicatedGamesConnections(dsn, read_only=True)

            def receipts():
                from .game_review import CurrentStudyReceiptReads, SystemClock
                return CurrentStudyReceiptReads(connections, SystemClock())

            def repertoire_readiness(repertoire_id):
                from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import (
                    read_repertoire, MAX_SOURCE_ROWS, MAX_GRAPH_ROWS, MAX_ROW_BYTES, MAX_SNAPSHOT_BYTES)
                from app.repertoire import project_published_repertoire
                with connections() as database:
                    facts = read_repertoire(database, repertoire_id)
                # Domain projection executes after its bounded reader is released.
                result = {"name": facts["repertoire"]["name"],
                    "learner_colors": sorted({row["trained_color"] for row in facts["lines"]}),
                    "publication_state": facts["repertoire"]["graph_state"],
                    "publication_revision": facts["repertoire"]["graph_generation"],
                    "source_rows": len(facts["lines"]), "graph_rows": len(facts["graphs"]),
                    "source_bytes": len(json.dumps(facts, ensure_ascii=False).encode()),
                    "limits": dict(source_rows=MAX_SOURCE_ROWS,graph_rows=MAX_GRAPH_ROWS,
                                   row_bytes=MAX_ROW_BYTES,snapshot_bytes=MAX_SNAPSHOT_BYTES)}
                try:
                    snapshot = project_published_repertoire(facts)
                    result.update(availability="available", learner_color=snapshot.learner_color,
                        mapping_count=len(snapshot.decision_presentations),
                        available_mapping_count=sum(mapping.available for mapping in snapshot.decision_presentations))
                except ValueError as error:
                    result.update(availability="unavailable",diagnostic=str(error))
                return result
            reader = CollectionObservations(connections, receipts=receipts,
                                            repertoire_readiness=repertoire_readiness)
        else:
            reader = reader_factory()
        report.update(reader.report(workspace=options.workspace, limit=options.limit,
                                    provider=options.provider, game_id=options.game_id))
    try:
        candidate = subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[3]),
            "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
        revision = candidate.stdout.strip() if candidate.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        revision = None
    report["identity"].update(observed_at=datetime.now(timezone.utc).isoformat(),
        candidate_revision=revision, runtime_revision=None,
        installation_provenance="TEMPO_DATABASE_READ_URL" if dsn else None)
    report["unresolved"].append("runtime_identity_and_global_delivery_disablement_not_observed")
    for provider in report["providers"].get("items", []):
        for field, present in provider["required_inputs"].items():
            if not present:
                report["unresolved"].append(provider["provider"] + ":configuration." + field + "_absent")
    if report["collection"].get("unresolved_games"):
        report["unresolved"].append("retained_game_comparisons_unresolved")
    for name in ("identity", "providers", "collection", "delivery", "selected_game"):
        section = report.get(name)
        if section and section.get("availability") == "unavailable":
            report["unresolved"].append(name + "_unavailable")
    encoded = _encode(report, private=options.private)
    print(encoded)
    return 2 if json.loads(encoded).get("truncated") or any(report[name].get("availability") == "unavailable" for name in
                    ("identity", "providers", "collection", "delivery")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
