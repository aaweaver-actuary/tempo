"""Existing bounded authored-route traversal over supplied cache facts."""
from ..domain import inventory_segment


def route_segment(route: dict, artifact: dict, color: str) -> tuple[int, list[str], bool, list[dict], str]:
    first = artifact['cursor']
    moves = route['moves'][first:first+16]
    terminal = first+len(moves)==len(route['moves'])
    occurrences,end_fen = ([],artifact['next_fen']) if artifact['complete'] else inventory_segment(artifact['next_fen'],moves,color,first,terminal=terminal)
    return first, moves, terminal, occurrences, end_fen
