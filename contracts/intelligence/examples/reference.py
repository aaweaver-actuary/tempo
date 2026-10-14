"""Consumer ordering illustration; authoritative checks are supplied by Learning."""

from datetime import datetime
from tempo_intelligence_contracts import v1


def suggested_order(
    availability,
    *,
    expected_policy_id: str,
    expected_policy_revision: int,
    workspace_id,
    publication_id,
    generation,
    learning_revision,
    eligible_card_ids,
    admitted_capacity,
    now,
):
    deterministic = sorted(set(eligible_card_ids))
    advice = availability.publication
    compatible = (
        advice is not None
        and availability.workspace_id == workspace_id
        and advice.content_publication_id == publication_id
        and advice.content_publication_generation == generation
        and advice.based_on_learning_revision == learning_revision
        and advice.valid_until is not None
        and datetime.fromisoformat(advice.produced_at.replace("Z", "+00:00"))
        <= now
        < datetime.fromisoformat(advice.valid_until.replace("Z", "+00:00"))
    )
    if (
        not compatible
        or not isinstance(advice.advisory, v1.PolicyRanking)
        or advice.advisory.policy_id != expected_policy_id
        or advice.advisory.policy_revision != expected_policy_revision
    ):
        return deterministic[:admitted_capacity]
    ranked = [
        item.card_id for item in advice.advisory.cards if item.card_id in deterministic
    ]
    return (
        ranked + [identity for identity in deterministic if identity not in ranked]
    )[:admitted_capacity]
