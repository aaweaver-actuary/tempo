"""Consumer-maintained decisions; independent of producer construction."""

import json
from tempo_learning_contracts import v1


def consume(record):
    model = getattr(v1, record["model"])
    value = v1.validate_contract(model, json.dumps(record["payload"]))
    assert value.workspace_id == "workspace-a"
    if record["model"] == "ReviewSettlement":
        settlement = value.settlement
        if settlement.status == "committed":
            assert settlement.receipt.submission_id == value.submission_id
            assert settlement.receipt.learning_revision > 1
            return "confirm_review"
        assert not hasattr(settlement, "receipt")
        return "keep_unconfirmed"
    if record["model"] == "QueueIssuance":
        assert bool(value.attempts) == (value.status == "issued")
        return "install_attempts" if value.status == "issued" else "wait_or_empty"
    if record["model"] == "AttemptIssuance":
        assert value.attempt.exercise.card_id == "card-a"
        specification = value.attempt.exercise.specification
        if specification.type == "move_line":
            assert specification.accepted_lines == [["e2e4"]]
        elif specification.type == "choice":
            assert specification.correct_option_ids == ["e4"]
        elif specification.type == "square_set":
            assert specification.required == ["e4"]
        elif specification.type == "knight_path":
            assert specification.target_squares == ["c3"]
        else:
            assert specification.rubric == "Explain central control"
    if record["model"] == "LearningObligation":
        assert value.source_kind == "game_evidence"
    return "validated_learning_input"
