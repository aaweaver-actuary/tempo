"""Game read input and comparison/import response shaping; no I/O."""


def validate_game_page_limit(limit):
    if not 1 <= limit <= 100:
        raise ValueError("page_limit_out_of_range")


def comparison_report_response(row, delivery):
    report = row["report"] or {"schema_version":1,"game":row["document"],"readiness":"pending","findings":[]}
    report['delivery'] = delivery
    if report["game"]["revision"] != row["document"]["revision"]:
        report["readiness"] = "pending"
        report["reason"] = "source_revision_changed"
    elif (report.get("snapshot") or {}).get("digest") != row["current_snapshot_digest"]:
        report["readiness"] = "pending"
        report["reason"] = "snapshot_changed"
    return report


def build_comparison_report(game, revision, snapshot, snapshot_digest, findings):
    return {"schema_version": 1, "game": game, "comparison_revision": revision,
            "readiness": "unknown" if not snapshot or not snapshot.serving_current() or "source_error" in game else "ready",
            "reason": game.get("source_error") or ("snapshot_missing" if not snapshot else None),
            "snapshot": {"digest": snapshot_digest, "snapshot_id": snapshot.snapshot_id,
                "revision": snapshot.revision, "publication_id": snapshot.publication.publication_id,
                "publication_generation": snapshot.publication.publication_generation,
                "source_revision": snapshot.publication.source_revision} if snapshot else None,
            "findings": findings}


def import_status_response(result, import_id):
    if not result:
        raise KeyError(import_id)
    result["next_attempt_at"]=result["next_attempt_at"].isoformat()
    return result
