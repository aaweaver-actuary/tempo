"""Cheap, revision-fenced foreground study-preference writes."""
from .command_gateway import register_command
from .learning_priorities import LearningPriorityUpdate, update_learning_priority


def update_policy_learning_priority(database, payload):
    request = LearningPriorityUpdate.model_validate(payload["request"])
    return update_learning_priority(database, payload["policy_id"], request, postgres=True)


register_command("repertoires.learning_priority.update", update_policy_learning_priority)
