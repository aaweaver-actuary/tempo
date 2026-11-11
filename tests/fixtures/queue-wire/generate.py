"""Generate synthetic wire fixtures by executing the current producer functions.

The AST extraction avoids importing the server composition root and starting its
providers. All storage reads and issuance are replaced with disposable inputs.
Run from the repository root with backend dependencies on PYTHONPATH.
"""
import argparse
import ast
import json
import logging
import sys
import types
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from starlette.responses import JSONResponse

from app.study.policy.opening_decision_evidence import decision_manifest
from app.study.queue_serving import (assemble_queue_cards, add_encounter_evidence,
                                     finalize_queue_cards, queue_payload, exercise_snapshot)

ROOT = Path(__file__).resolve().parents[3]
FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
DAY = '2026-11-01'

class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 1, 12, 0, tzinfo=tz)


def extract(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    for node in selected:
        node.decorator_list = []
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)


queue_commands = types.ModuleType('app.queue_commands')
queue_commands.current_study_day = lambda db: DAY
sys.modules['app.queue_commands'] = queue_commands

def row(identifier, entry, family):
    return dict(id=identifier, queue_entry_id=entry, start_fen=FEN,
                moves_json=json.dumps(['e2e4']), content_type=family,
                repertoire_name='Fixture', repertoire_source='synthetic', revision=2,
                latest_failed_review_id=0, prefix_split_rejected_after_review_id=0,
                effective_trained_color='white',
                **({'study_id':'study-1', 'study_exercise_id':'exercise-1', 'repertoire_id':None, 'kind':'exercise'} if family == 'study_exercise' else {}))

opening = row('opening-1', 21, 'opening')
opening.update(opening_evidence_origin_queue_entry_id=20, opening_evidence_parent_attempt_id='original-attempt',
               position=1, cycle=1, attempt_state='again', attempt_failed=1)
defense = row('defense-1', 24, 'defense')
defense.update(source_ref='candidate-1', admission_source='defense:candidate-1')
endgame = row('saved-endgame-1', 25, 'endgame')
endgame.update(source_ref='saved-endgame', repertoire_id='endgames')
rows = [opening, row('tactic-1', 22, 'tactic'), row('exercise-card', 23, 'study_exercise'),
        defense, endgame, row('middlegame-1', 26, 'middlegame')]

state = SimpleNamespace(rows=rows, available=True)
storage = SimpleNamespace(
    evidence_timezone=lambda db: ['UTC'], queue_projection=lambda db, day: None,
    position_pending_modes=lambda db, cards: {},
    queue_diagnostics=lambda db, day: [], queue_rows=lambda db, day, limit, **kw: state.rows,
    encounter_badges=lambda db, cards, cutoff: {} if state.available else (_ for _ in ()).throw(RuntimeError('unavailable')),
    exercise_revision=lambda db, exercise: {'current_revision': 3, 'specification_json': json.dumps({'type':'square_set', 'prompt':'Select e4', 'hint':'', 'explanation':'', 'required':['e4'], 'optional':[], 'candidate_region':None, 'criterion':'e4'})},
)
manifest_storage = SimpleNamespace(read_connection=lambda: nullcontext(None), queue_context_rows=lambda db, args: [dict(
    id=4, queue_entry_id=21, card_id='opening-1', revision=2, start_fen=FEN,
    moves_json=json.dumps(['e2e4']), trained_color='white', effective_trained_color='white', repertoire_id='rep-1')])
manifest_ns = dict(__name__='fixture_manifest', json=json, storage=manifest_storage, read_connection=manifest_storage.read_connection, decision_manifest=decision_manifest)
extract('backend/app/study/position_checkpoint.py', {'_manifest_snapshot', 'queue_manifests'}, manifest_ns)
from app.study import position_checkpoint
position_checkpoint.queue_manifests = manifest_ns['queue_manifests']
issued_heads = []
def capture_head(day, card, **capabilities):
    issued_heads.append((day, card['queue_entry_id'], card['id'], card['revision']))
    return None, {'issued': True}, None

# Inject at the current composition capability, retaining the real Study operation.
from app.product_composition import study as study_composition
study_composition._issue_facts = capture_head
study_composition.datetime = FixtureClock

ns = dict(__name__='app.main', JSONResponse=JSONResponse, json=json, logging=logging, datetime=FixtureClock, timedelta=timedelta, timezone=timezone,
          read_connection=lambda: nullcontext(None), queue_projection_storage=storage,
          postgres_store=SimpleNamespace(configured=lambda: True), MISS_REASON='miss',
          assemble_queue_cards=assemble_queue_cards,
          add_encounter_evidence=add_encounter_evidence, finalize_queue_cards=finalize_queue_cards,
          queue_payload=queue_payload, exercise_snapshot=exercise_snapshot)
extract('backend/app/main.py', {'_queue_payload', 'prepared_queue'}, ns)
ns['queue_manifests'] = manifest_ns['queue_manifests']
available = ns['prepared_queue'](True)
state.available = False
unavailable = ns['_queue_payload'](include_opening_evidence=True)
state.rows = []
empty = ns['_queue_payload']()
assert issued_heads == [(DAY, 21, 'opening-1', 2)] * 2
parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true', help='compare without modifying the fixture')
parser.add_argument('--fixture', type=Path, default=Path(__file__).with_name('producer.json'))
arguments = parser.parse_args()
output = json.dumps(dict(available=available, unavailable=unavailable, empty=empty), indent=2, sort_keys=True) + '\n'
if arguments.check:
    if arguments.fixture.read_text() != output:
        raise SystemExit('Queue producer fixture is stale; regenerate producer.json')
else:
    arguments.fixture.write_text(output)
