"""Read-only discovery preserves all alternatives and refuses partial results."""
from copy import deepcopy

import pytest

from tempo_legacy_database.adapters.games.authority.candidate_lookup import (
    CandidateOverflow, decision_bindings,
)


def test_discovery_preserves_conflicting_and_ineligible_physical_alternatives():
    rows = [dict(card_id="one", moves_json='["e2e4"]', introduced_at="2026-01-01",
                 integrity_blocked=False, deleted=False, archived=0),
            dict(card_id="two", moves_json='["d2d4"]', introduced_at=None,
                 integrity_blocked=True, deleted=True, archived=1),
            dict(card_id="missing", revision=None, introduced_at=None)]
    calls = []
    def read(statement, parameters):
        calls.append(parameters)
        return deepcopy(rows)
    assert decision_bindings(read, "current", 7, "position") == tuple(rows)
    assert calls == [("current", 7, "position")]


def test_discovery_refuses_the_sixty_fifth_binding_without_partial_selection():
    def read(_statement, _parameters):
        return [dict(card_id=f"candidate-{i}") for i in range(65)]
    with pytest.raises(CandidateOverflow, match="decision_binding_candidate_budget"):
        decision_bindings(read, "current", 7, "position")


@pytest.mark.parametrize("args", [("", 1, "p"), ("r", 0, "p"), ("r", True, "p"),
                                  ("r", 1, ""), ("r", 1, "x" * 201)])
def test_discovery_requires_explicit_bounded_scope_before_read(args):
    def forbidden(*_):
        pytest.fail("invalid scope reached database")
    with pytest.raises(ValueError, match="one_explicit_current_position_required"):
        decision_bindings(forbidden, *args)
