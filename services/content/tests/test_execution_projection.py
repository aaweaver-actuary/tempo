"""Explicit learner decisions inside unchanged physical opening executables."""
import chess
import pytest
from tempo_content.models import SourceExercise,ExerciseBinding,SourceSnapshot
from tempo_content.domain import ContentError,build_publication,validate_exercise_step


def learner_exercise():
    board=chess.Board();board.push_uci('e2e4')
    return {'key':'historical-card','decision_key':'historical-card','policy_key':'black',
        'starting_fen':board.fen(en_passant='fen'),'prompt':'Play the continuation',
        'specification':{'type':'move_line','mode':'stepwise_line','grading_policy':'reference','accepted_lines':[['e7e5','g1f3','b8c6']]},
        'execution_prelude':{'starting_fen':chess.STARTING_FEN,'moves':['e2e4']}}


def test_C3_original_physical_executable_survives_explicit_opponent_prelude():
    exercise=SourceExercise.model_validate(learner_exercise())
    assert validate_exercise_step(exercise) is None
    source=SourceSnapshot.model_validate({'schema_version':1,'workspace_id':'test','source_key':'captured','title':'Captured',
        'repertoires':[{'key':'black','title':'Black','learner_color':'black'}],
        'policies':[{'key':'black','repertoire_key':'black'}],
        'routes':[{'key':'line','starting_fen':chess.STARTING_FEN,'moves':['e2e4','e7e5','g1f3','b8c6']}],
        'memberships':[{'repertoire_key':'black','policy_key':'black','route_key':'line'}],
        'exercises':[exercise.model_dump(mode='json')],
        'bindings':[{'route_key':'line','policy_key':'black','ply':1,'exercise_key':exercise.key}]})
    publication=build_publication(source,1,1,'2026-10-17T00:00:00Z',{exercise.key:1})
    definition=publication.definitions[0]
    assert definition.starting_fen==chess.STARTING_FEN
    assert definition.specification.accepted_lines==[['e2e4','e7e5','g1f3','b8c6']]
    assert definition.exercise_revision==1


def test_C3_wrong_prelude_cannot_claim_original_executable_equivalence():
    raw=learner_exercise();raw['execution_prelude']['moves']=['d2d4']
    exercise=SourceExercise.model_validate(raw)
    with pytest.raises(ContentError,match='prelude'):
        validate_exercise_step(exercise)


def test_C3_execution_extensions_retain_old_source_bytes_when_absent():
    raw=learner_exercise();del raw['execution_prelude']
    assert 'execution_prelude' not in SourceExercise.model_validate(raw).model_dump(mode='json')
    binding=ExerciseBinding(route_key='line',policy_key='black',ply=1,exercise_key='card')
    assert 'exercise_ply' not in binding.model_dump(mode='json')
    assert ExerciseBinding.model_validate({**binding.model_dump(mode='json'),'exercise_ply':2}).exercise_ply==2


def test_C3_interior_projection_reconstructs_exact_position_and_move():
    from tempo_content.domain import project_move_line,physical_execution
    exercise=SourceExercise.model_validate(learner_exercise())
    fen,specification=physical_execution(exercise)
    projected,line=project_move_line(fen,specification,3)
    board=chess.Board();[board.push_uci(move) for move in ['e2e4','e7e5','g1f3']]
    assert projected==board.fen(en_passant='fen')
    assert line['accepted_lines']==[['b8c6']] and line['mode']=='single'
    with pytest.raises(ContentError,match='outside'):
        project_move_line(fen,specification,4)
