"""Read-only Learning receipt/frontier facts on the caller's transaction."""


def historical_delivery_receipt(database, delivery_id):
    historical = database.execute(
        "SELECT digest,result_json FROM evidence_deliveries WHERE delivery_id=?",
        (delivery_id,),
    ).fetchone()
    return historical


def accepted_revision_frontier(database, workspace_id, evidence_id):
    frontier = database.execute(
        "SELECT evidence_revision FROM accepted_evidence_revisions WHERE workspace_id=? AND evidence_id=?",
        (workspace_id, evidence_id),
    ).fetchone()
    return frontier
