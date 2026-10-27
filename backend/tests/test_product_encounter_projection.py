"""The shared projector consumes one exact target with retained route aliases."""
from copy import deepcopy

import pytest

from app.contracts.product_v02.repertoire import IntroductionGameScope
from app.games.feedback.comparison import compare_record, normalize_record
from app.games.feedback.coordinator import project_encounters
from test_product_game_findings import equivalent_routes, record


def inputs(*, count=62, played="d2d4"):
    source=record((played,))
    snapshot=equivalent_routes(source,count)
    game=normalize_record(source,provider="lichess",account="Learner",workspace_id="workspace")
    findings=compare_record(source,provider="lichess",account="Learner",workspace_id="workspace",snapshot=snapshot)
    scope=IntroductionGameScope(provider="lichess",game_id=game.game_id,
        source_revision=game.source_revision,source_digest=game.source_digest)
    return game,snapshot,findings,scope


def project(game,snapshot,findings,scope):
    return project_encounters(game,findings=findings,snapshots=(snapshot,),source=scope,
        detected_at="2026-10-27T04:00:00Z",occurred_at=game.played_at)


@pytest.mark.parametrize("count",[2,62])
@pytest.mark.parametrize("played",["e2e4","d2d4"])
def test_projector_emits_one_original_equivalent_target_without_dropping_provenance(count,played):
    game,snapshot,findings,scope=inputs(count=count,played=played)
    before=deepcopy(snapshot.model_dump())
    command,=project(game,snapshot,findings,scope)
    assert command.decision in snapshot.decision_presentations
    assert command.decision.route_id=="alias-00"
    assert command.observation==("matched" if played=="e2e4" else "missed")
    assert command.key.occurrence_ply==1 and command.key.decision_id=="shared-decision"
    assert len(snapshot.decision_presentations)==len(snapshot.membership.routes)==count
    assert snapshot.model_dump()==before
    reversed_snapshot=snapshot.model_copy(update={
        "decision_presentations":tuple(reversed(snapshot.decision_presentations))})
    repeated,=project(game,reversed_snapshot,findings,scope)
    assert repeated==command and repeated.key.logical_id()==command.key.logical_id()


@pytest.mark.parametrize("change",[
    {"available":False},
    {"introduced_before_game":None,"introduction_game":None},
    {"introduction_matches_current_content":False},
])
def test_projector_keeps_conflicting_alias_facts_instead_of_filtering_them_away(change):
    game,snapshot,findings,scope=inputs()
    mappings=list(snapshot.decision_presentations)
    mappings[-1]=mappings[-1].model_copy(update=change)
    changed=snapshot.model_copy(update={"decision_presentations":tuple(mappings)})
    assert project(game,changed,findings,scope)==()
    assert len(changed.decision_presentations)==62
