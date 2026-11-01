"""Generate synthetic wire fixtures by executing the current producer functions.

The AST extraction avoids importing the server composition root and starting its
providers. All storage reads and issuance are replaced with disposable inputs.
Run from the repository root with backend dependencies and Learning runtime on PYTHONPATH.
"""
import ast
import json
import logging
import sys
import types
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

runtime = types.ModuleType("tempo_learning_runtime")
runtime.__path__ = [str(Path(__file__).resolve().parents[3] / "services/learning/runtime/src/tempo_learning_runtime")]
sys.modules[runtime.__name__] = runtime
from tempo_learning_runtime.opening_decision_evidence import decision_manifest

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
next_card = types.ModuleType('app.services.queue_next_card')
next_card.pending_next_card_preparation = lambda db, day: False
sys.modules['app.services.queue_next_card'] = next_card

def row(identifier, entry, family):
    return dict(id=identifier, queue_entry_id=entry, start_fen=FEN,
                moves_json=json.dumps(['e2e4']), content_type=family,
                repertoire_name='Fixture', repertoire_source='synthetic', revision=2,
                latest_failed_review_id=0, prefix_split_rejected_after_review_id=0,
                effective_trained_color='white',
                **({'study_id':'study-1', 'study_exercise_id':'exercise-1', 'repertoire_id':None, 'kind':'exercise'} if family == 'study_exercise' else {}))

opening = row('opening-1', 21, 'opening')
opening.update(opening_evidence_origin_queue_entry_id=20, opening_evidence_parent_attempt_id='original-attempt')
rows = [opening, row('tactic-1', 22, 'tactic'), row('exercise-card', 23, 'study_exercise')]
state = SimpleNamespace(rows=rows, available=True)
storage = SimpleNamespace(
    evidence_timezone=lambda db: ['UTC'], queue_projection=lambda db, day: None,
    queue_diagnostics=lambda db, day: [], queue_rows=lambda db, day, limit, **kw: state.rows,
    encounter_badges=lambda db, cards, cutoff: {} if state.available else (_ for _ in ()).throw(RuntimeError('unavailable')),
    exercise_revision=lambda db, exercise: {'current_revision': 3, 'specification_json': json.dumps({'type':'square_set', 'prompt':'Select e4', 'hint':'', 'explanation':'', 'required':['e4'], 'optional':[], 'candidate_region':None, 'criterion':'e4'})},
)
manifest_storage = SimpleNamespace(read_connection=lambda: nullcontext(None), queue_context_rows=lambda db, args: [dict(
    id=4, queue_entry_id=21, card_id='opening-1', revision=2, start_fen=FEN,
    moves_json=json.dumps(['e2e4']), trained_color='white', effective_trained_color='white', repertoire_id='rep-1')])
manifest_ns = dict(__name__='fixture_manifest', json=json, storage=manifest_storage, decision_manifest=decision_manifest)
extract('backend/app/services/postgres_opening_evidence.py', {'_manifest_snapshot', 'queue_manifests'}, manifest_ns)
manifest_module = types.ModuleType('app.services.postgres_opening_evidence')
manifest_module.queue_manifests = manifest_ns['queue_manifests']
sys.modules[manifest_module.__name__] = manifest_module
ns = dict(__name__='app.main', json=json, logging=logging, datetime=FixtureClock, timedelta=timedelta, timezone=timezone,
          read_connection=lambda: nullcontext(None), queue_projection_storage=storage,
          postgres_store=SimpleNamespace(configured=lambda: True), MISS_REASON='miss',
          _issue_returned_queue_head=lambda day, card: None)
extract('backend/app/main.py', {'_queue_payload', 'prepared_queue'}, ns)
ns['queue_manifests'] = manifest_ns['queue_manifests']
available = ns['prepared_queue'](True)
state.available = False
unavailable = ns['_queue_payload'](include_opening_evidence=True)
state.rows = []
empty = ns['_queue_payload']()
Path(__file__).with_name('producer.json').write_text(json.dumps(dict(available=available, unavailable=unavailable, empty=empty), indent=2, sort_keys=True) + '\n')
