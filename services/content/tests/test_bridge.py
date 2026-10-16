"""Named A import/proposal regressions run in regular installed consumers."""
import hashlib
import json
from copy import deepcopy

import chess
import pytest
from tempo_content.bridge import ImportManifest, ResponseProposal, map_import, digest
from tempo_content.domain import ContentError, canonical_source, stable_id
from tempo_content.domain import validate_exercise_step
from tempo_content.models import SourceExercise


def manifest(raw):
    return {"schema_version":1,"kind":"authored_import","workspace_id":"bridge-test","source_key":"opening",
        "title":"Original", "complete":True,"provenance":{"kind":"synthetic","source_sha256":hashlib.sha256(raw).hexdigest(),"description":"Explicit synthetic test"},
        "repertoires":[{"key":"white","title":"White","learner_color":"white"}],"policies":[{"key":"main","repertoire_key":"white"}],
        "route_assignments":[],"legacy_aliases":{},"reentry":[]}


def authored_pgn():
    return b'[Event "Title"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 *\n'


def test_A_accepted_alternatives_validate_from_the_same_starting_position():
    exercise=SourceExercise.model_validate({'key':'root','decision_key':'root','policy_key':'main','starting_fen':chess.STARTING_FEN,'prompt':'Play either response',
        'specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[['e2e4'],['d2d4']]}})
    assert validate_exercise_step(exercise) is None


def test_A_identity_rename_permutation_uses_stable_policy_decisions():
    raw = authored_pgn()
    description = manifest(raw)
    description["route_assignments"]=[{"fingerprint":digest([chess.STARTING_FEN,["e2e4","e7e5","g1f3"]]),"route_key":"route","policy_key":"main"}]
    source,_ = map_import(raw,description)
    renamed = raw.replace(b'Title',b'Renamed')
    successor = deepcopy(description)
    successor["title"]="Renamed source"
    successor["provenance"]["source_sha256"]=hashlib.sha256(renamed).hexdigest()
    other,_ = map_import(renamed,successor)
    assert source.exercises == other.exercises
    assert [stable_id(source.workspace_id,source.source_key,"card",item.key) for item in source.exercises] == [stable_id(other.workspace_id,other.source_key,"card",item.key) for item in other.exercises]
    assert len(source.exercises)==2


def test_A_import_requires_provenance_color_and_exact_declared_scope():
    raw = authored_pgn()
    description = manifest(raw)
    with pytest.raises(ContentError,match="explicit stable"):
        map_import(raw,description)
    description["provenance"]["source_sha256"]="0"*64
    with pytest.raises(ContentError,match="Input bytes"):
        map_import(raw,description)
    del description["repertoires"][0]["learner_color"]
    with pytest.raises(ValueError):
        ImportManifest.model_validate(description)


def test_A_proposal_is_not_approval_and_requires_legal_executable_content():
    proposal={"schema_version":1,"kind":"response_proposal","status":"draft","proposal_id":"one",
        "workspace_id":"bridge-test","source_key":"opening","repertoire_key":"white","policy_key":"main","learner_color":"white",
        "expected_source_revision":1,"source_digest":"0"*64,"route_key":"route","anchor_ply":1,"opponent_move":"c7c5",
        "decision_fen":"rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2",
        "accepted_lines":[["g1f3"]],"provenance":{"kind":"synthetic","evidence_refs":["game:1"]}}
    assert ResponseProposal.model_validate(proposal).status=="draft"
    for mutation in ({"kind":"gap_context"},{"status":"approved"},{"accepted_lines":[]},{"accepted_lines":[["g1g5"]]},
                     {'provenance':{}},{'provenance':{'kind':'synthetic','evidence_refs':[]}}):
        with pytest.raises(ValueError):
            ResponseProposal.model_validate({**proposal,**mutation})
