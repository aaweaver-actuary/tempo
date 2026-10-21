"""Comparison response/readiness shaping over captured facts; no I/O."""


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
