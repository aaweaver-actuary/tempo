from datetime import datetime, timezone
from tempo_game_evidence_contracts import v1, v2
from tempo_learning_remediation import AcceptedDecision, ReceiveContext

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
DECISION = AcceptedDecision("card", 1, "decision", "publication", 1, "position", ("e2e4", "c2c4"),
                            "workspace", aliases=("route-alias",))


class Authority:
    def __init__(self, accepted=DECISION):
        self.accepted = accepted

    def decision(self, card_id, decision_id, publication_id, publication_generation):
        return self.accepted


def delivery(*, game="game", evidence="evidence", revision=1, obligation="obligation", card="card", ply=0,
             transport="delivery", occurred_at="2026-10-14T12:00:00Z", contract=2):
    data = dict(contract_version=contract, workspace_id="workspace", delivery_id=transport,
        evidence_id=evidence, evidence_revision=revision, game_id=game, game_revision=1,
        encounters=[dict(encounter_id="encounter",ply=ply,position_key="position",decision_id="decision",observed_move="d2d4")],
        comparisons=[dict(encounter_id="encounter",card_id=card,decision_id="decision",publication_id="publication",publication_generation=1,
                          expected_moves=["e2e4","c2c4"],status="missed")],
        misses=[dict(miss_id="miss",encounter_id="encounter",decision_id="decision",reason="outside_repertoire")],
        obligations=[dict(obligation_id=obligation,source_evidence_id=evidence,source_revision=revision,card_id=card,
                          decision_id="decision",publication_generation=1,kind="real_game_miss")])
    if contract == 2:
        data.update(occurred_at=occurred_at,withdrawals=[])
        data["obligations"][0].update(obligation_revision=revision,card_revision=1,publication_id="publication",encounter_id="encounter",miss_id="miss")
    return (v2 if contract == 2 else v1).EvidenceDelivery.model_validate(data)


def context(value, *, received_at=NOW, accepted=DECISION):
    return ReceiveContext("workspace",value.game_id,value.game_revision,value.evidence_id,value.evidence_revision,received_at,Authority(accepted))


def withdrawal(value, *, revision=2, obligation_revision=1):
    data = value.model_dump(mode="json")
    data.update(delivery_id="withdrawal-"+str(revision),evidence_revision=revision,
                encounters=[],comparisons=[],misses=[],obligations=[],withdrawals=[dict(
                    obligation_id=value.obligations[0].obligation_id,expected_obligation_revision=obligation_revision,reason="source_corrected")])
    return v2.EvidenceDelivery.model_validate(data)
