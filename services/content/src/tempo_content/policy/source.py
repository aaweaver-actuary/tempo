"""Existing authored-source decisions over supplied facts; no database or transport."""
from __future__ import annotations

import json
from typing import Callable

import chess
from tempo_content_publication_contracts.v1 import PublicationValidation

from ..domain import ContentError, canonical_position, canonical_source, project_move_line, stable_id, validate_exercise_step
from ..models import SourceExercise, SourceSnapshot


def prepare_source(snapshot: SourceSnapshot | dict, expected_revision: int, workspace_id: str,
                   *, digest_text: Callable[[str], str]) -> tuple[SourceSnapshot, dict, str, str, str, str]:
    if type(expected_revision) is not int or expected_revision<0:
        raise ContentError('invalid_source_revision','Expected revision must be a nonnegative integer')
    snapshot = SourceSnapshot.model_validate(snapshot)
    if snapshot.workspace_id != workspace_id:
        raise ContentError('workspace_mismatch','Source belongs to another workspace')
    canonical = canonical_source(snapshot)
    payload = json.loads(canonical)
    digest = digest_text(canonical)
    source_id = stable_id(workspace_id,snapshot.source_key,'source',snapshot.source_key)
    publication_id = stable_id(workspace_id,snapshot.source_key,'publication','source')
    stage_id = stable_id(workspace_id,snapshot.source_key,'stage',digest)
    return snapshot, payload, digest, source_id, publication_id, stage_id


def exercise_continuation(exercise: SourceExercise, state: dict | None, learner_color: str) -> dict | None:
    continuation = validate_exercise_step(exercise,state)
    if exercise.specification.type=='move_line' and chess.Board(exercise.starting_fen).turn!=(learner_color=='white'):
        raise ContentError('exercise_learner_color_mismatch','Move exercise must start on its policy learner turn')
    return continuation


def binding_responses(exercise: dict, binding: dict, route: dict | None, occurrence: dict | None) -> list[str]:
    if not occurrence or not route['complete']:
        raise ContentError('incomplete_binding','Route occurrence is not complete')
    decision_fen,specification=project_move_line(exercise['starting_fen'],exercise['specification'],binding.get('exercise_ply',0))
    if occurrence['opponent'] or occurrence['position_key']!=canonical_position(decision_fen):
        raise ContentError('invalid_binding','Binding must match the declared learner decision in its exercise')
    accepted = [line[0] for line in specification['accepted_lines']] if specification['type']=='move_line' else []
    if accepted and occurrence['authored_move'] and specification['grading_policy']=='reference' and occurrence['authored_move'] not in accepted:
        raise ContentError('route_policy_conflict','Authored response differs from executable policy')
    return accepted


def publication_report(workspace_id: str, status: dict, definition_count: int) -> dict:
    validation = 'validated' if status['last_complete_revision']==status['requested_revision'] else 'rejected' if status['state']=='failed' else 'retryable_failure'
    errors = [] if validation=='validated' else [status['error']['code'] if status['error'] else 'retirement_required' if not definition_count else 'inventory_incomplete']
    return {'publication_id':status['publication_id'],'publication_generation':status['generation'],'source_revision':status['revision'],'last_complete_generation':status['last_complete_generation'],'stale':status['stale'],'validation':PublicationValidation(contract_version=1,workspace_id=workspace_id,publication_id=status['publication_id'],publication_generation=status['generation'],status=validation,errors=errors).model_dump(mode='json'),'learning_compatibility':{'definition_count':definition_count,'bundle_limit':128,'status':'definition_limit_exceeded' if definition_count>128 else 'requires_execution_adapter','activation':'not_requested'}}


def coverage_reply(reply: dict, authored: bool, direct: dict | None, transposed: dict | None) -> dict:
    return {**reply,'legal':True,'authored':authored,'covered':bool(direct),'transposition_supported':bool(transposed) and not bool(direct),'status':'covered' if direct else 'transposition_supported' if transposed else 'uncovered'}


def inventory_report(source: dict, task: dict, definition_count: int) -> dict:
    state = 'retirement_required' if task['state']=='complete' and definition_count==0 else task['state']
    return {**source,**task,'state':state,'status':state,'stale':source['last_complete_revision']!=source['requested_revision']}
