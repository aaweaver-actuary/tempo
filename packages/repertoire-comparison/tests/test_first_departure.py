import pytest
from tempo_repertoire_comparison import first_departure
from .test_comparison import game, route, snapshot


@pytest.mark.parametrize("played,expected_ply,side", [
    (["d2d4"], 1, "learner"),
    (["e2e4", "c7c5"], 2, "opponent"),
    (["e2e4", "e7e5"], None, None),
])
def test_first_departure_normal_cases(played, expected_ply, side):
    result = first_departure(game(played), snapshot([route(["e2e4", "e7e5"])]))
    assert result == dict(status="compared", ply=expected_ply, side=side, reason=None)
