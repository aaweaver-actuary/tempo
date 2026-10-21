"""Existing exercise-phase application sequence over Content-owned operations."""
from collections.abc import Callable

from ..models import SourceExercise
from ..policy import exercise_continuation
from .application import Hook


def reconcile_exercise(task: dict, manifest: dict, hook: Hook | None, *,
                       transition: Callable[..., dict], read_item: Callable[..., dict],
                       commit_exercise: Callable[[dict, dict, SourceExercise, dict | None, Hook | None], bool],
                       notify: Callable[..., None]) -> dict:
    if task['cursor']>=manifest['counts']['exercises']:
        return transition(task,'bindings',hook=hook)
    payload = read_item(manifest['stage_id'],'exercises',task['cursor'])
    exercise = SourceExercise.model_validate(payload)
    policy = read_item(manifest['stage_id'],'policies',key=exercise.policy_key)
    repertoire = read_item(manifest['stage_id'],'repertoires',key=policy['repertoire_key'])
    notify(hook,'after_read',task=task)
    continuation = exercise_continuation(exercise,task['exercise_state'],repertoire['learner_color'])
    notify(hook,'before_commit',task=task)
    if not commit_exercise(task,payload,exercise,continuation,hook):
        return {'worked':True,'status':'pending','phase':'exercises','validation_pending':True}
    return {'worked':True,'status':'pending','phase':'exercises','validated':exercise.key}
