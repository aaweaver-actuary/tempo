"""Existing binding-phase application sequence over Content-owned operations."""
from collections.abc import Callable

from ..policy import binding_responses
from .application import Hook


def reconcile_binding(task: dict, manifest: dict, hook: Hook | None, *,
                      transition: Callable[..., dict], read_item: Callable[..., dict],
                      read_target: Callable[[dict, dict], tuple[dict | None, dict | None]],
                      commit_binding: Callable[[dict, dict, dict, list[str], Hook | None], None],
                      notify: Callable[..., None]) -> dict:
    if task['cursor']>=manifest['counts']['bindings']:
        return transition(task,'complete',state='complete',hook=hook)
    binding = read_item(manifest['stage_id'],'bindings',task['cursor'])
    route, occurrence = read_target(task,binding)
    exercise = read_item(manifest['stage_id'],'exercises',key=binding['exercise_key'])
    notify(hook,'after_read',task=task)
    accepted = binding_responses(exercise,binding,route,occurrence)
    notify(hook,'before_commit',task=task)
    commit_binding(task,binding,occurrence,accepted,hook)
    return {'worked':True,'status':'pending','phase':'bindings','validated':binding['exercise_key']}
