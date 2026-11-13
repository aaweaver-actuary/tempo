"""Choose an existing eligible carrier for an authored Study decision."""

from tempo_evidence_math.position_learning import Hold


def select_carrier(source, target, *, card_id=None):
    """Choose one current physical carrier for an exact authored decision.

    Aliases and multiple physical candidates within one repertoire are normal;
    choose deterministically without treating physical identity as schedule identity.
    """
    key = target.key
    if source.authored_token.repertoire_id != key.repertoire_id:
        return Hold("position_carrier_source_scope_mismatch")
    decisions = [
        decision
        for route in source.routes
        for decision in route.decisions
        if decision.definition.key == key
    ]
    if not decisions or any(d.definition != target for d in decisions):
        return Hold("position_carrier_definition_ambiguous")
    bindings = [
        binding
        for binding in source.snapshot.decision_presentations
        if binding.repertoire_id == key.repertoire_id
        and binding.position_key == key.position
    ]
    if any(
        binding.selected_response_uci != target.expected_uci for binding in bindings
    ):
        return Hold("position_carrier_response_ambiguous")
    eligible = [
        binding
        for binding in bindings
        if binding.available
        and binding.presentation_id
        and binding.presentation_revision > 0
    ]
    if card_id is not None:
        eligible = [
            binding for binding in eligible if binding.presentation_id == card_id
        ]
    if not eligible:
        return Hold("position_carrier_unavailable")
    return min(
        eligible,
        key=lambda binding: (
            binding.presentation_id,
            binding.presentation_revision,
            binding.decision_id,
        ),
    )
