"""Queries and deterministic persistence helpers for tactical opportunities."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..database import connection


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
    clauses = [
        "o.active=1", "o.outcome IN ('exploited','missed')",
        "g.adaptive_excluded=0", "g.analysis_state IN ('ready','complete')",
        "o.analysis_version=g.analysis_version",
    ]
    parameters: list[str] = []
    for column, key in (("g.played_at", "from_date"), ("g.played_at", "to_date")):
        value = filters.get(key)
        if value:
            clauses.append(f"date({column}) {'>=' if key == 'from_date' else '<='} date(?)")
            parameters.append(value)
    for column, key in (("g.provider", "provider"), ("g.speed", "speed"), ("g.color", "color"), ("o.motif", "motif"), ("o.outcome", "outcome")):
        value = filters.get(key)
        if value:
            clauses.append(f"{column}=?")
            parameters.append(value)
    with connection() as database:
        rows = database.execute(
            f"""SELECT o.*,g.played_at,g.provider,g.speed,g.color
                FROM tactical_opportunities o JOIN imported_games g ON g.id=o.game_id
                WHERE {' AND '.join(clauses)}
                ORDER BY g.played_at DESC,o.ply,o.motif""", parameters,
        ).fetchall()
    grouped: dict[str, list] = {}
    pin_breakdown: dict[str, dict] = {}
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
                label: _summary(bucket_rows) for label, bucket_rows in {
                    label: [row for row in motif_rows if row["id"] in {item["id"] for item in pin_rows}]
                    for label, pin_rows in pin_breakdown.items()
                }.items()
            }} if motif == "pin" else {})}
            for motif, motif_rows in sorted(grouped.items())
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
