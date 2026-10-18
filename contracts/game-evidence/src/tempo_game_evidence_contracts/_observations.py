"""Structural evidence linkage shared by explicit wire variants."""


def coherent_observations(delivery, linkage_suffix):
    encounters = {item.encounter_id: item for item in delivery.encounters}
    misses = {item.miss_id: item for item in delivery.misses}
    for items, identity in ((delivery.encounters, "encounter_id"), (delivery.misses, "miss_id"),
                            (delivery.obligations, "obligation_id"), (delivery.withdrawals, "obligation_id")):
        if len({getattr(item, identity) for item in items}) != len(items):
            raise ValueError("duplicate evidence identity")
    if not delivery.encounters and not delivery.withdrawals:
        raise ValueError("empty delivery must explicitly withdraw a contribution")
    if {item.obligation_id for item in delivery.obligations} & {item.obligation_id for item in delivery.withdrawals}:
        raise ValueError("cannot add and withdraw the same obligation")
    for comparison in delivery.comparisons:
        encounter = encounters.get(comparison.encounter_id)
        if encounter is None or comparison.decision_id != encounter.decision_id:
            raise ValueError("comparison must identify its encounter decision")
    for miss in delivery.misses:
        encounter = encounters.get(miss.encounter_id)
        if encounter is None or miss.decision_id != encounter.decision_id:
            raise ValueError("miss must identify its encounter decision")
    for obligation in delivery.obligations:
        if (obligation.source_evidence_id, obligation.source_revision) != (delivery.evidence_id, delivery.evidence_revision):
            raise ValueError("obligation must preserve evidence lineage")
        encounter = encounters.get(obligation.encounter_id)
        if encounter is None or encounter.decision_id != obligation.decision_id:
            raise ValueError("obligation must identify its encounter decision")
        comparisons = [item for item in delivery.comparisons if
                      (item.encounter_id, item.card_id, item.decision_id, *linkage_suffix(item))
                      == (obligation.encounter_id, obligation.card_id, obligation.decision_id, *linkage_suffix(obligation))]
        if len(comparisons) != 1:
            raise ValueError("obligation requires one explicit comparison")
        if obligation.kind == "real_game_miss":
            miss = misses.get(obligation.miss_id)
            if (miss is None or (miss.encounter_id, miss.decision_id) !=
                    (obligation.encounter_id, obligation.decision_id) or comparisons[0].status != "missed"):
                raise ValueError("miss obligation requires its missed comparison and miss")
        elif obligation.miss_id is not None:
            raise ValueError("observed encounter must not claim a miss")
    return delivery
