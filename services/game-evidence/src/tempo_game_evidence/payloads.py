"""Bounded v2 evidence scopes and explicit source-specific withdrawals."""
from tempo_game_evidence_contracts.v2 import EvidenceDelivery

from .source import identity

BUCKET_PLIES = 64


def make_deliveries(game, findings, revision, publication, prior=()):
    definitions = {item.card_id:item for item in publication.definitions} if publication else {}
    # Only possibly accepted contributions are candidates for withdrawal. Omission
    # in a later publication is never itself a withdrawal.
    heads = {}
    for previous in sorted(prior,key=lambda item:item["evidence_revision"]):
        if not previous["attempts"] or previous["status"] == "superseded":
            continue
        payload = previous["payload"]
        if not payload or payload.get("contract_version") != 2:
            raise ValueError("legacy_evidence_requires_explicit_correction_adapter")
        if previous["status"] in {"terminal_failure","conflict","stale_revision"}:
            continue
        bucket = previous["bucket"]
        current = heads.setdefault(bucket,{})
        for withdrawal in payload["withdrawals"]:
            current.pop(withdrawal["obligation_id"],None)
        for obligation in payload["obligations"]:
            current[obligation["obligation_id"]] = obligation
    by_bucket = {}
    for finding in findings:
        if finding["status"] != "unknown":
            by_bucket.setdefault(finding["ply"]//BUCKET_PLIES,[]).append(finding)
    deliveries = []
    for bucket in sorted(set(heads)|set(by_bucket)):
        known = by_bucket.get(bucket,[])
        misses = [item for item in known if item["status"] == "missed"]
        evidence_id = identity("evidence",game["game_id"],bucket)
        obligations = [{"obligation_id":identity("miss_obligation",item["encounter_id"]),
            "obligation_revision":revision,"source_evidence_id":evidence_id,"source_revision":revision,
            "card_id":item["card_id"],"card_revision":definitions[item["card_id"]].exercise_revision,
            "decision_id":item["decision_id"],"publication_id":publication.publication_id,
            "publication_generation":publication.publication_generation,
            "encounter_id":item["encounter_id"],"miss_id":identity("miss",item["encounter_id"]),"kind":"real_game_miss"}
            for item in misses]
        current_ids = {item["obligation_id"] for item in obligations}
        withdrawals = [{"obligation_id":obligation_id,"expected_obligation_revision":previous["obligation_revision"],
                        "reason":"reanalysis"} for obligation_id,previous in heads.get(bucket,{}).items()
                       if obligation_id not in current_ids]
        if not obligations and not withdrawals:
            continue
        payload = EvidenceDelivery.model_validate({
            "contract_version":2,"workspace_id":game["workspace_id"],"delivery_id":identity(evidence_id,revision),
            "evidence_id":evidence_id,"evidence_revision":revision,"game_id":game["game_id"],"game_revision":game["revision"],
            "occurred_at":game.get("played_at"),
            "encounters":[{"encounter_id":item["encounter_id"],"ply":item["ply"],
                "position_key":item.get("decision_position_key",item["position_key"]),
                "decision_id":item["decision_id"],"observed_move":item["move"]} for item in known],
            "comparisons":[{"encounter_id":item["encounter_id"],"card_id":item["card_id"],
                "decision_id":item["decision_id"],"publication_id":publication.publication_id,
                "publication_generation":publication.publication_generation,"expected_moves":item["expected_moves"],
                "status":item["status"]} for item in known],
            "misses":[{"miss_id":identity("miss",item["encounter_id"]),"encounter_id":item["encounter_id"],
                "decision_id":item["decision_id"],"reason":item["explanation"]} for item in misses],
            "obligations":obligations,"withdrawals":withdrawals,
        }).model_dump(mode="json")
        deliveries.append((bucket,payload))
    return deliveries


def make_delivery(game,findings,revision,publication):
    """Small-fixture convenience; the persistence owner uses bounded scopes."""
    batches = make_deliveries(game,findings,revision,publication)
    if len(batches)>1:
        raise ValueError("multiple_evidence_scopes_required")
    return batches[0][1] if batches else None
