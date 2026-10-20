"""The extracted frontier retains GE traversal, including legacy endpoints."""
from tempo_repertoire_comparison import RouteFrontier, TraversalRoute


def test_frontier_retains_terminal_reentry_binding_supported_by_legacy_ge():
    frontier = RouteFrontier([TraversalRoute(("start", "end"), ("move",), (1,))], "elsewhere")
    candidates = frontier.candidates("end")
    assert candidates == {(0, 1)}
    frontier.advance(candidates, "another-move")
    assert frontier.active == set()


def test_frontier_advances_ordered_occurrences_without_implicit_fen_reentry():
    frontier = RouteFrontier([TraversalRoute(("start", "middle", "start", "end"), ("a", "b", "c"))], "start")
    assert frontier.candidates("start") == {(0, 0)}
    frontier.advance(frontier.candidates("start"), "wrong")
    assert frontier.candidates("middle") == set()
    assert frontier.candidates("start") == set()


def test_frontier_retains_distinct_route_occurrences_at_explicit_anchor():
    frontier = RouteFrontier([TraversalRoute(("start", "middle", "start", "end"), ("a", "b", "c"), (2,))], "start")
    assert frontier.candidates("start") == {(0, 0), (0, 2)}
    frontier.advance(frontier.candidates("start"), "c")
    assert frontier.active == {(0, 3)}
