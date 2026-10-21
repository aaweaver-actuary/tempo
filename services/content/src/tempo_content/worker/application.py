"""One existing reconciliation delivery over exact Content-owned operations."""
from __future__ import annotations

from collections.abc import Callable

from ..domain import ContentError

Hook = Callable[[str, dict], None]
Slice = Callable[[dict, dict, Hook | None], dict]


def reconcile_once(source_key: str | None, hook: Hook | None, *,
                   claim_task: Callable[[str | None, Hook | None], dict | None],
                   collect: Callable[..., dict],
                   read_manifest: Callable[[str, int], dict],
                   route_slice: Slice, exercise_slice: Slice, binding_slice: Slice,
                   fail_task: Callable[[dict, str, ValueError], bool]) -> dict:
    task = claim_task(source_key,hook)
    if not task:
        return collect(hook=hook)
    manifest = read_manifest(task['source_id'],task['revision'])
    try:
        if task['phase']=='routes':
            return route_slice(task,manifest,hook)
        if task['phase']=='exercises':
            return exercise_slice(task,manifest,hook)
        if task['phase']=='bindings':
            return binding_slice(task,manifest,hook)
        raise ContentError('unknown_task_phase',task['phase'])
    except ValueError as error:
        code = error.code if isinstance(error,ContentError) else 'invalid_content'
        if code=='stale_generation':
            return {'worked':False,'status':'stale_generation','phase':task['phase']}
        if not fail_task(task,code,error):
            return {'worked':False,'status':'stale_generation','phase':task['phase']}
        return {'worked':True,'status':'failed','phase':task['phase'],'error':{'code':code,'message':str(error)}}
