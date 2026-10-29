"""Position discovery is only a lead; complete capture/source fencing follows."""
from copy import deepcopy
import json

import chess
import pytest

from app.services import retained_history_capture as owner


@pytest.fixture
def discovery(monkeypatch):
    game=dict(id="game",color="white",result="draw",start_fen=chess.STARTING_FEN,
        played_at="2026-10-20T12:00:00Z",moves_json=json.dumps(["e2e4","e7e5","g1f3"]))
    state=dict(graph_state="ready",integrity_current=True,graph_generation=3)
    monkeypatch.setattr(owner.headers,"imported_game",lambda *_: [deepcopy(game)])
    monkeypatch.setattr(owner,"decision_bindings",lambda *_: ({"card_id":None,"archived":1},))
    return game,state


def test_discovery_does_not_filter_missing_or_blocked_binding_before_complete_capture(discovery):
    game,state=discovery
    calls=[]
    def capture(rep,game_id,plies):
        calls.append((rep,game_id,plies))
        return dict(game=deepcopy(game),catalogue_scope={"scope":{"decision_plies":list(plies)}})
    result=owner.capture_history_positions("rep","game",start_ply=2,maximum_plies=1,
        reader=lambda *_: None,metadata=lambda _: deepcopy(state),capture=capture)
    assert calls==[("rep","game",(2,))] and not result.unresolved
    assert result.positions==1 and result.remaining_positions==0


@pytest.mark.parametrize("changed",["game","publication"])
def test_discovery_refuses_changed_game_or_publication_for_the_entire_slice(discovery,changed):
    game,state=discovery
    def capture(rep,game_id,plies):
        result=dict(game=deepcopy(game),catalogue_scope={"scope":{"decision_plies":list(plies)}})
        if changed=="game": game["moves_json"]='["d2d4"]'
        else: state["graph_generation"]+=1
        return result
    with pytest.raises(ValueError,match="source_changed"):
        owner.capture_history_positions("rep","game",start_ply=2,maximum_plies=1,
            reader=lambda *_: None,metadata=lambda _: deepcopy(state),capture=capture)


@pytest.mark.parametrize("start,maximum",[(-1,1),(0,17),(True,1)])
def test_history_position_interval_refuses_invalid_bound_before_read(monkeypatch,start,maximum):
    monkeypatch.setattr(owner.headers,"imported_game",lambda *_: pytest.fail("read attempted"))
    with pytest.raises(ValueError,match="bounded_history_ply_interval"):
        owner.capture_history_positions("rep","game",start_ply=start,maximum_plies=maximum)
