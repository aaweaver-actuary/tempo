import copy
import json

import chess
import pytest

from tempo_game_evidence import ComparisonSnapshot, compare_game, normalize_pgn, replay_positions
from tempo_game_evidence.store import make_delivery
from .factories import observed, pgn, snapshot


@pytest.mark.parametrize("move,status",[("e2e4","matched"),("d2d4","matched"),("c2c4","missed")])
def test_accepted_alternative_is_not_a_repertoire_miss(move,status):
    game,positions = observed([move])
    result = compare_game(game,positions,ComparisonSnapshot.model_validate(snapshot(expected=["e2e4","d2d4"])))
    assert result[0]["status"]==status
    assert result[0]["expected_moves"]==["d2d4","e2e4"]
    assert (make_delivery(game,result,1,ComparisonSnapshot.model_validate(snapshot()).publication) is not None)==(status=="missed")


def test_opponent_departure_does_not_create_a_learner_miss():
    game,positions = observed(["e2e4","c7c5","g1f3"])
    result = compare_game(game,positions,ComparisonSnapshot.model_validate(snapshot()))
    assert [item["status"] for item in result]==["matched","unknown"]
    assert [item["ply"] for item in result]==[0,2]


def test_unaccepted_publication_and_missing_route_metadata_remain_unknown():
    game,positions = observed(["c2c4"])
    for value in [snapshot(activation=False),{**snapshot(),"routes":[]}]:
        result = compare_game(game,positions,ComparisonSnapshot.model_validate(value))
        assert result[0]["status"]=="unknown"


def test_italian_policy_is_not_applied_to_ryu_lopez_route():
    value = snapshot(moves=["e2e4","e7e5","g1f3","b8c6","f1c4"],binding_ply=4)
    second = copy.deepcopy(value["publication"]["definitions"][0])
    second.update(card_id="ruy-card",decision_id="ruy-decision",policy_id="ruy-policy")
    second["specification"]["accepted_lines"]=[["f1b5"]]
    value["publication"]["definitions"].append(second)
    route = copy.deepcopy(value["routes"][0])
    route.update(route_id="ruy",repertoire_id="ruy")
    route["moves"][-1]="f1b5"
    route["bindings"][0]["card_id"]="ruy-card"
    value["routes"].append(route)
    game,positions = observed(["e2e4","e7e5","g1f3","b8c6","f1b5"])
    findings = compare_game(game,positions,ComparisonSnapshot.model_validate(value))
    assert findings[-1]["status"]=="matched" and findings[-1]["policy_id"]=="ruy-policy"
    game,positions = observed(["e2e4","e7e5","g1f3","b8c6","d2d4"])
    assert compare_game(game,positions,ComparisonSnapshot.model_validate(value))[-1]["status"]=="unknown"


def test_shared_route_membership_produces_one_logical_occurrence():
    value = snapshot()
    route = copy.deepcopy(value["routes"][0])
    route.update(route_id="shared-route",repertoire_id="another-repertoire")
    value["routes"].append(route)
    game,positions = observed(["c2c4"])
    findings = compare_game(game,positions,ComparisonSnapshot.model_validate(value))
    assert len(findings)==1 and len(findings[0]["memberships"])==2
    delivery = make_delivery(game,findings,1,ComparisonSnapshot.model_validate(value).publication)
    assert len(delivery["obligations"])==1
    assert make_delivery(game,findings,2,ComparisonSnapshot.model_validate(value).publication)["obligations"][0]["obligation_id"]==delivery["obligations"][0]["obligation_id"]


@pytest.mark.parametrize("reentry,status",[(False,"unknown"),(True,"matched")])
def test_transposition_after_divergence_requires_explicit_reentry(reentry,status):
    value = snapshot(moves=["g1f3","d7d5","g2g3","c7c5","f1g2"],binding_ply=4,reentry=reentry)
    game,positions = observed(["g2g3","c7c5","g1f3","d7d5","f1g2"])
    assert compare_game(game,positions,ComparisonSnapshot.model_validate(value))[-1]["status"]==status


@pytest.mark.parametrize("fen,moves,color",[
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 37",["e1g1","e8c8"],"white"),
    ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 37",["e5d6"],"white"),
    ("8/P7/8/8/8/8/7p/K5k1 b - - 0 37",["h2h1q"],"black"),
])
def test_custom_positions_use_board_side_and_relative_ply(fen,moves,color):
    game,positions = observed(moves,fen=fen,color=color)
    value = ComparisonSnapshot.model_validate(snapshot(fen=fen,moves=moves,color=color))
    finding = compare_game(game,positions,value)[0]
    assert finding["status"]=="matched" and finding["ply"]==0 and finding["turn"]==color
    assert len(positions)==len(moves)+1
    assert positions[0]["position_key"]==" ".join(fen.split()[:4])


def test_repeated_positions_have_distinct_occurrence_ids():
    moves=["g1f3","g8f6","f3g1","f6g8","g1f3"]
    value = snapshot(moves=moves)
    value["routes"][0]["bindings"].append({"ply":4,"card_id":"card-shared"})
    game,positions = observed(moves)
    findings = compare_game(game,positions,ComparisonSnapshot.model_validate(value))
    assert findings[0]["position_key"]==findings[-1]["position_key"]
    assert findings[0]["encounter_id"]!=findings[-1]["encounter_id"]


def test_date_only_pgn_does_not_invent_game_time():
    assert normalize_pgn(pgn(["e2e4"]),"Learner")["played_at"] is None


@pytest.mark.parametrize("mutate",[
    lambda value:value["activation"].update(publication_generation=2),
    lambda value:value["routes"][0]["bindings"][0].update(ply=99),
    lambda value:value["routes"][0].update(learner_color="black"),
    lambda value:value["publication"]["definitions"][0]["specification"].update(accepted_lines=[["e2e5"]]),
])
def test_invalid_snapshot_provenance_and_routes_are_rejected(mutate):
    value=snapshot()
    mutate(value)
    with pytest.raises(ValueError):
        ComparisonSnapshot.model_validate(value)


def test_illegal_pgn_and_multiple_games_are_rejected():
    with pytest.raises(ValueError):
        normalize_pgn(pgn(["e2e4"])+pgn(["d2d4"]),"Learner")
    with pytest.raises(ValueError):
        normalize_pgn(b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e5 1-0',"Learner")


def test_large_game_evidence_is_split_into_stable_bounded_scopes():
    from tempo_game_evidence.payloads import make_deliveries
    route_moves=["g1h3","g8h6","h3g1","h6g8"]*140
    value=snapshot(moves=route_moves,reentry=True)
    value["routes"][0]["bindings"]=[{"ply":ply,"card_id":"card-shared","reentry":True} for ply in range(0,560,4)]
    game,positions=observed(["g1f3","g8f6","f3g1","f6g8"]*140)
    parsed=ComparisonSnapshot.model_validate(value)
    findings=compare_game(game,positions,parsed)
    batches=make_deliveries(game,findings,1,parsed.publication)
    assert sum(len(payload["obligations"]) for _,payload in batches)==140
    assert len(batches)>1
    assert all(len(payload["encounters"])<=32 for _,payload in batches)
    assert len({payload["evidence_id"] for _,payload in batches})==len(batches)


def test_card_replacement_does_not_rebind_an_existing_obligation_identity():
    from tempo_game_evidence.payloads import make_deliveries
    game,positions=observed(["c2c4"])
    old_snapshot=ComparisonSnapshot.model_validate(snapshot())
    old_findings=compare_game(game,positions,old_snapshot)
    old_payload=make_delivery(game,old_findings,1,old_snapshot.publication)
    value=snapshot()
    value["publication"]["definitions"][0]["card_id"]="replacement-card"
    value["routes"][0]["bindings"][0]["card_id"]="replacement-card"
    current=ComparisonSnapshot.model_validate(value)
    findings=compare_game(game,positions,current)
    prior=[{"evidence_revision":1,"attempts":1,"status":"completed","payload":old_payload,"bucket":0}]
    _,payload=make_deliveries(game,findings,2,current.publication,prior)[0]
    assert payload["obligations"][0]["obligation_id"]!=old_payload["obligations"][0]["obligation_id"]
    assert payload["withdrawals"][0]["obligation_id"]==old_payload["obligations"][0]["obligation_id"]


def test_C3_shared_policy_prefix_is_unknown_before_authoritative_anchor():
    from .factories import snapshot, observed
    from tempo_game_evidence.snapshot import parse_snapshot
    from tempo_game_evidence.comparison import compare_game
    document=snapshot()
    document['routes'][0]['authority_start_ply']=3
    game,positions=observed(['d2d4'])
    findings=compare_game(game,positions,parse_snapshot(document))
    assert findings[0]['status']=='unknown'
    assert findings[0]['card_id'] is None


def test_C3_worker_reuses_only_byte_identical_validated_snapshot(monkeypatch):
    from tempo_game_evidence import store as module
    parsed=[]
    def parse(document):
        value=object();parsed.append((document,value));return value
    monkeypatch.setattr(module,'parse_snapshot',parse)
    worker=module.EvidenceStore('explicit-test-only')
    first=worker.comparison_snapshot('one',{'revision':1})
    assert worker.comparison_snapshot('one',{'revision':1}) is first
    assert len(parsed)==1
    assert worker.comparison_snapshot('one',{'revision':2}) is not first
    worker.comparison_snapshot('two',{'revision':2})
    assert len(parsed)==3
    assert worker._validated_snapshot[0][0]=='two'
