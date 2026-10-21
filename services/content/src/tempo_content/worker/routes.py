"""Existing bounded authored-route traversal over supplied cache facts."""
from collections.abc import Callable

from ..domain import canonical_position, inventory_segment
from .application import Hook

RouteSegment = tuple[int, list[str], bool, list[dict], str]


def route_segment(route: dict, artifact: dict, color: str) -> tuple[int, list[str], bool, list[dict], str]:
    first = artifact['cursor']
    moves = route['moves'][first:first+16]
    terminal = first+len(moves)==len(route['moves'])
    occurrences,end_fen = ([],artifact['next_fen']) if artifact['complete'] else inventory_segment(artifact['next_fen'],moves,color,first,terminal=terminal)
    return first, moves, terminal, occurrences, end_fen


def reconcile_route(task: dict, manifest: dict, hook: Hook | None, *,
                    transition: Callable[..., dict], read_item: Callable[..., dict],
                    read_artifact: Callable[[dict, str], tuple[str, dict | None]],
                    register_route: Callable[[dict, dict, dict, str, str, Hook | None], None],
                    commit_segment: Callable[[dict, dict, str, dict, RouteSegment, Hook | None], bool],
                    notify: Callable[..., None]) -> dict:
    if task['cursor']>=manifest['counts']['memberships']:
        return transition(task,'exercises',hook=hook)
    membership = read_item(manifest['stage_id'],'memberships',task['cursor'])
    route = read_item(manifest['stage_id'],'routes',key=membership['route_key'])
    repertoire = read_item(manifest['stage_id'],'repertoires',key=membership['repertoire_key'])
    color = repertoire['learner_color']
    artifact_id, artifact = read_artifact(route,color)
    notify(hook,'after_read',task=task,artifact=artifact)
    if artifact is None:
        # Chess validation and artifact construction occur without a pool lease.
        canonical_position(route['starting_fen'])
        notify(hook,'before_commit',task=task)
        register_route(task,membership,route,color,artifact_id,hook)
        return {'worked':True,'status':'pending','phase':'routes','registered':artifact_id,'plies':0}
    segment = route_segment(route,artifact,color)
    first, moves, terminal, occurrences, end_fen = segment
    notify(hook,'before_commit',task=task,artifact=artifact)
    if not commit_segment(task,membership,artifact_id,artifact,segment,hook):
        return {'worked':False,'status':'artifact_advanced','phase':'routes'}
    return {'worked':True,'status':'pending','phase':'routes','plies':len(moves),'occurrences':len(occurrences),'reused':artifact['complete']}
