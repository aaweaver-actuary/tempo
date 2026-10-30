"""The shared projector consumes one exact target with retained route aliases."""
from copy import deepcopy

import pytest

from app.contracts.product_v02.repertoire import IntroductionGameScope
from app.games.feedback.comparison import compare_game, compare_record, normalize_record
from app.games.feedback.coordinator import project_encounters
from test_product_game_findings import equivalent_routes, record


def inputs(*, count=62, played="d2d4", introduction=None):
    source=record((played,))
    snapshot=equivalent_routes(source,count)
    if introduction is not None:
        snapshot=snapshot.model_copy(update={"decision_presentations":tuple(
            mapping.model_copy(update={"introduction":introduction,"introduced_at":None,
                "introduced_before_game":None,"introduction_game":None})
            for mapping in snapshot.decision_presentations)})
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


@pytest.mark.parametrize("introduction", ["unknown", "introduced"])
def test_exact_available_unknown_introduction_chronology_is_preserved_as_encounter(introduction):
    game,snapshot,findings,scope=inputs(count=2,introduction=introduction)
    finding,=findings
    assert finding.kind=="known_unavailable" and finding.diagnostic=="introduction_evidence_unavailable"
    assert finding.membership=="present" and not finding.lapse_eligible
    assert finding.introduced_before_game is None
    before=deepcopy((snapshot.model_dump(),finding.model_dump()))

    command,=project_encounters(game,findings=findings,snapshots=(snapshot,),source=scope,
        detected_at="2026-10-29T12:00:00Z",occurred_at=None)
    assert command.source==scope and command.decision in snapshot.decision_presentations
    assert command.payload_digest==game.source_digest
    assert command.key.game_id==game.game_id and command.key.decision_id==finding.decision_id
    assert command.key.occurrence_ply==finding.ply==1
    assert command.observation=="missed" and command.played_uci==finding.played_uci=="d2d4"
    assert command.expected_uci==finding.expected_uci[0]=="e2e4"
    assert command.occurred_at is None and command.detected_at=="2026-10-29T12:00:00Z"
    assert command.decision.introduction==introduction and command.decision.introduced_at is None
    assert command.decision.introduced_before_game is command.decision.introduction_game is None
    assert (snapshot.model_dump(),finding.model_dump())==before
    repeated,=project_encounters(game,findings=findings,snapshots=(snapshot,),source=scope,
        detected_at=command.detected_at,occurred_at=None)
    assert repeated==command and repeated.key.logical_id()==command.key.logical_id()


@pytest.mark.parametrize("change", [{"available":False}, {"introduction":"introduced"}])
def test_unknown_chronology_encounter_requires_all_exact_alias_facts_to_agree(change):
    game,snapshot,findings,scope=inputs(count=2,introduction="unknown")
    mappings=list(snapshot.decision_presentations)
    mappings[-1]=mappings[-1].model_copy(update=change)
    changed=snapshot.model_copy(update={"decision_presentations":tuple(mappings)})
    assert project(game,changed,findings,scope)==()


@pytest.mark.parametrize("boundary,diagnostic", [
    ("incomplete","snapshot_incomplete_or_unavailable"),
    ("unavailable","current_presentation_unavailable"),
])
def test_other_unavailable_findings_do_not_become_unknown_chronology_encounters(boundary,diagnostic):
    game,snapshot,_,scope=inputs(count=2,introduction="unknown")
    if boundary=="incomplete":
        snapshot=snapshot.model_copy(update={"membership":snapshot.membership.model_copy(update={"complete":False})})
    else:
        change={"available":False}
        snapshot=snapshot.model_copy(update={"decision_presentations":tuple(
            mapping.model_copy(update=change) for mapping in snapshot.decision_presentations)})
    findings=compare_game(game,provider="lichess",time_precision="second",snapshot=snapshot)
    finding,=findings
    assert finding.kind=="known_unavailable" and finding.diagnostic==diagnostic
    assert not finding.lapse_eligible
    assert project(game,snapshot,findings,scope)==()


@pytest.mark.parametrize("change,message", [
    ({"source_digest":"b"*64},"finding differs from supplied source"),
    ({"repertoire_revision":2},"outside supplied snapshot scope"),
    ({"played_uci":"e2e4"},"played move differs from captured game"),
])
def test_unknown_chronology_encounter_retains_source_scope_and_played_move_guards(change,message):
    game,snapshot,findings,scope=inputs(count=2,introduction="unknown")
    changed=findings[0].model_copy(update=change)
    with pytest.raises(ValueError,match=message):
        project(game,snapshot,(changed,),scope)


@pytest.mark.parametrize("boundary", ["revised", "imprecise"])
def test_exact_authored_difference_without_lapse_proof_still_projects(boundary):
    game, snapshot, _, scope = inputs(count=2, introduction="introduced")
    snapshot = snapshot.model_copy(update={"decision_presentations": tuple(mapping.model_copy(update={
        "introduced_at": "2026-10-01T00:00:00Z", "introduction_matches_current_content": boundary != "revised"})
        for mapping in snapshot.decision_presentations)})
    findings = compare_game(game, provider="lichess", snapshot=snapshot,
        time_precision="day" if boundary == "imprecise" else "second")
    finding, = findings
    assert finding.kind == "known_unavailable" and not finding.lapse_eligible
    encounter, = project(game, snapshot, findings, scope)
    assert encounter.observation == "missed" and encounter.decision in snapshot.decision_presentations
    assert encounter.decision.introduced_at == "2026-10-01T00:00:00Z"


def test_repeated_projection_findings_emit_one_logical_occurrence():
    game, snapshot, findings, scope = inputs(count=2)
    command, = project(game, snapshot, findings + findings, scope)
    assert command.key.occurrence_ply == 1


@pytest.mark.parametrize("change", [
    {"presentation_id": "different-card"}, {"presentation_revision": 2},
    {"decision_id": "different-decision"}, {"selected_response_uci": "d2d4"},
])
def test_projector_does_not_filter_away_conflicting_target_identity(change):
    game, snapshot, findings, scope = inputs(count=2, introduction="unknown")
    mappings = list(snapshot.decision_presentations)
    mappings[-1] = mappings[-1].model_copy(update=change)
    snapshot = snapshot.model_copy(update={"decision_presentations": tuple(mappings)})
    assert project(game, snapshot, findings, scope) == ()
    assert project(game, snapshot, findings, scope) == project(game,
        snapshot.model_copy(update={"decision_presentations": tuple(reversed(mappings))}), findings, scope)


def test_projector_keeps_conflicting_same_logical_key_as_findings_without_command():
    game, snapshot, findings, scope = inputs(count=2, introduction="unknown")
    other = snapshot.model_copy(update={
        "membership": snapshot.membership.model_copy(update={"scope_repertoire_ids": ("other",)}),
        "decision_presentations": tuple(mapping.model_copy(update={"repertoire_id": "other"})
            for mapping in snapshot.decision_presentations)})
    other_finding = findings[0].model_copy(update={"repertoire_id": "other"})
    assert project_encounters(game, findings=findings + (other_finding,), snapshots=(snapshot, other),
        source=scope, detected_at="2026-10-29T12:00:00Z", occurred_at=game.played_at) == ()
