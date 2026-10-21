"""Queries and deterministic persistence helpers for tactical opportunities."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters.projections import tactical_opportunities as storage
from tempo_legacy_database.adapters import postgres_store


def opportunity_id(game_id: str, analysis_version: int, ply: int, motif: str) -> str:
    import hashlib

    return hashlib.sha256(f"{game_id}\0{analysis_version}\0{ply}\0{motif}".encode()).hexdigest()


def _percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def _summary(rows: list) -> dict:
    opportunities = len(rows)
    exploited = sum(row["outcome"] == "exploited" for row in rows)
    missed_rows = [row for row in rows if row["outcome"] == "missed"]
    confidence_values = [float(row["confidence"]) for row in rows]
    return {
        "opportunities": opportunities,
        "exploited": exploited,
        "missed": len(missed_rows),
        "conversion_rate": _percentage(exploited, opportunities),
        "average_missed_centipawn_cost": round(sum(row["evaluation_loss_cp"] for row in missed_rows) / len(missed_rows), 1) if missed_rows else None,
        "supporting_games": len({row["game_id"] for row in rows}),
        "confidence": {
            "sample_size": opportunities,
            "mean": round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None,
            "minimum": min(confidence_values) if confidence_values else None,
        },
    }


def tactical_statistics(filters: dict[str, str | None] | None = None) -> dict:
    filters = filters or {}
    if postgres_store.configured():
        return _postgres_tactical_statistics(filters)
    with connection() as database:
        rows = storage.sqlite_tactical_rows(database, filters)
    grouped: dict[str, list] = {}
    pin_breakdown: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["motif"], []).append(row)
        if row["motif"] == "pin":
            evidence = json.loads(row["evidence_json"] or "{}")
            pin = next((item for item in evidence.get("motif_evidence", []) if item.get("motif") == "pin"), {})
            pin_type = pin.get("concrete_outcome", {}).get("pin_type", "unknown")
            bucket = "absolute" if pin_type == "absolute" else "relative" if pin_type == "relative" else "unknown"
            for label in (bucket, "existing" if pin.get("existed_before") else "created"):
                pin_breakdown.setdefault(label, []).append(row)
    return {
        "filters": filters,
        "overall": _summary(rows),
        "motifs": [
            {"motif": motif, **_summary(motif_rows), **({"pin_breakdown": {
                label: _summary(pin_rows) for label, pin_rows in pin_breakdown.items()
            }} if motif == "pin" else {})}
            for motif, motif_rows in sorted(grouped.items())
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _postgres_tactical_statistics(filters: dict[str, str | None]) -> dict:
    """Aggregate the populated game history in SQL; inspect only pin evidence."""
    with connection() as database:
        aggregates, pin_rows = storage.postgres_tactical_rows(database, filters)

    def summary(row) -> dict:
        opportunities = int(row["opportunities"])
        exploited = int(row["exploited"])
        return {
            "opportunities": opportunities, "exploited": exploited,
            "missed": int(row["missed"]),
            "conversion_rate": _percentage(exploited, opportunities),
            "average_missed_centipawn_cost": (
                round(float(row["missed_cost"]), 1)
                if row["missed_cost"] is not None else None
            ),
            "supporting_games": int(row["supporting_games"]),
            "confidence": {
                "sample_size": opportunities,
                "mean": (round(float(row["mean_confidence"]), 3)
                         if row["mean_confidence"] is not None else None),
                "minimum": row["minimum_confidence"],
            },
        }

    overall_row = next((row for row in aggregates if row["motif"] is None), None)
    if overall_row is None:
        overall = _summary([])
    else:
        overall = summary(overall_row)
    pin_breakdown: dict[str, list] = {}
    for row in pin_rows:
        evidence = json.loads(row["evidence_json"] or "{}")
        pin = next((item for item in evidence.get("motif_evidence", [])
                    if item.get("motif") == "pin"), {})
        pin_type = pin.get("concrete_outcome", {}).get("pin_type", "unknown")
        bucket = "absolute" if pin_type == "absolute" else "relative" if pin_type == "relative" else "unknown"
        for label in (bucket, "existing" if pin.get("existed_before") else "created"):
            pin_breakdown.setdefault(label, []).append(row)
    motifs = []
    for row in sorted((row for row in aggregates if row["motif"] is not None),
                      key=lambda item: item["motif"]):
        motif = row["motif"]
        item = {"motif": motif, **summary(row)}
        if motif == "pin":
            item["pin_breakdown"] = {
                label: _summary(bucket_rows) for label, bucket_rows in pin_breakdown.items()
            }
        motifs.append(item)
    return {"filters": filters, "overall": overall, "motifs": motifs,
            "generated_at": datetime.now(timezone.utc).isoformat()}
