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


def test_introduced_card_discovery_is_only_one_raw_position_lead():
    from tempo_legacy_database.adapters.games.authority.candidate_lookup import introduced_card_lead
    row = dict(card_id="lead", revision=2, introduced_at="2026-01-01")
    assert introduced_card_lead(lambda _s, _p: [row], "r", 1) == row
    assert introduced_card_lead(lambda _s, _p: [], "r", 1) is None
    with pytest.raises(CandidateOverflow, match="introduced_card_lead_budget"):
        introduced_card_lead(lambda _s, _p: [row, row], "r", 1)


def test_introduced_card_discovery_refuses_invalid_publication_before_read():
    from tempo_legacy_database.adapters.games.authority.candidate_lookup import introduced_card_lead
    with pytest.raises(ValueError):
        introduced_card_lead(lambda *_: pytest.fail("read attempted"), "r", True)


def test_single_card_lead_has_bounded_already_inspected_exclusions():
    from tempo_legacy_database.adapters.games.authority.candidate_lookup import introduced_card_lead
    calls = []
    def read(_statement, parameters):
        calls.append(parameters)
        return []
    assert introduced_card_lead(read, "r", 1, excluded_card_ids=("known",)) is None
    assert calls == [("r", 1, ["known"])]
    with pytest.raises(ValueError):
        introduced_card_lead(lambda *_: pytest.fail("read attempted"), "r", 1,
                             excluded_card_ids=tuple(str(i) for i in range(9)))
