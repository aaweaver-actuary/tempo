"""Bounded publication control; native PostgreSQL proves the persisted boundary."""
from types import SimpleNamespace
from contextlib import contextmanager
import json

import pytest

from app.services import postgres_integrity as integrity


def test_integrity_publication_accepts_large_completed_generation_without_hard_caps(monkeypatch):
    monkeypatch.setattr(integrity, 'lock_current_slice', lambda *_: True)
    monkeypatch.setattr(integrity, '_graph_generation_is_current', lambda *_: True)
    advances = []
    monkeypatch.setattr(integrity, 'advance_task_slice_in_transaction',
                        lambda _db, _task, **next_slice: advances.append(next_slice) or True)
    class CompletedPages:
        def execute_native(self, statement, parameters=()):
            if 'COUNT(*)' in statement:
                return SimpleNamespace(fetchone=lambda: (201,))
            return SimpleNamespace(fetchone=lambda: None)
    task = {'id':'scan','generation':7,'payload':{'repertoire_id':'large','graph_generation':7}}
    assert integrity.publish_integrity_issues_in_transaction(CompletedPages(), task)
    assert advances[0]['next_phase'] == 'validate_cards'


def test_integrity_large_issue_pages_close_database_before_parsing_and_resume(monkeypatch):
    active = False
    sources = json.dumps([{'type':'card','id':f'card-{index:03}'} for index in range(97)])
    issue = {'run_id':'scan:7','id':'issue','repertoire_id':'large','sources_json':sources}
    class Source:
        def execute_native(self, *_): return SimpleNamespace(fetchone=lambda: issue)
    @contextmanager
    def read(**options):
        nonlocal active
        assert options=={'authoritative':True}
        active=True
        try: yield Source()
        finally: active=False
    original_loads=json.loads
    def parse(text):
        assert not active,'Publication parsing occupied a transaction'
        return original_loads(text)
    monkeypatch.setattr(integrity,'background_read_connection',read)
    monkeypatch.setattr(integrity.json,'loads',parse)
    task={'id':'scan','generation':7,'payload':{}}
    page=integrity.prepare_integrity_publication_page(task)
    assert len(page.card_ids)==32 and page.source_offset==0 and not page.complete
    task['payload']={'publishing_issue_id':'issue','block_source_offset':96}
    page=integrity.prepare_integrity_publication_page(task)
    assert page.card_ids==('card-096',) and page.complete
    task['payload']['block_source_offset']=98
    with pytest.raises(ValueError,match='cursor'):
        integrity.prepare_integrity_publication_page(task)


def test_integrity_incomplete_generation_cannot_advance_to_validation(monkeypatch):
    monkeypatch.setattr(integrity,'lock_current_slice',lambda *_:True)
    monkeypatch.setattr(integrity,'_graph_generation_is_current',lambda *_:True)
    database=SimpleNamespace(execute_native=lambda *_:SimpleNamespace(fetchone=lambda:(1,)))
    task={'id':'scan','generation':7,'payload':{'repertoire_id':'large','graph_generation':7}}
    with pytest.raises(RuntimeError,match='incomplete publication pages'):
        integrity.publish_integrity_issues_in_transaction(database,task)


def test_integrity_stale_page_replay_cannot_write_any_publication(monkeypatch):
    monkeypatch.setattr(integrity,'lock_current_slice',lambda *_:False)
    def unexpected(*_): raise AssertionError('Stale replay reached SQL')
    assert not integrity.publish_integrity_issues_in_transaction(SimpleNamespace(execute_native=unexpected),{})


def test_integrity_compatible_failed_retry_preserves_publication_cursor(monkeypatch):
    from app import activity_commands
    failed={'id':'scan','kind':'integrity_scan','deduplication_key':'large','generation':7,
            'priority':40,'state':'failed','phase':'publish','attempt_count':5,'max_attempts':5,
            'next_attempt_at':None,'created_at':'2026-10-09T00:00:00+00:00',
            'updated_at':'2026-10-09T00:00:00+00:00','last_error':'interrupted',
            'payload_json':json.dumps({'repertoire_id':'large','graph_generation':7,'publishing_issue_id':'issue','block_source_offset':64})}
    statements=[]
    class SavedPages:
        def execute_native(self,statement,parameters=()):
            statements.append((statement,parameters))
            if statement.startswith('SELECT * FROM background_tasks'): row=failed
            elif 'SELECT scan_generation' in statement: row=('scan:7',)
            else: row=None
            return SimpleNamespace(fetchone=lambda:row)
    monkeypatch.setattr(integrity,'_graph_generation_is_current',lambda *_:True)
    activity_commands.retry_failed_task(SavedPages(),{'task_id':'scan'})
    update=next(parameters for statement,parameters in statements if statement.startswith('UPDATE background_tasks'))
    assert update[0]=='publish'
    assert all('payload_json=' not in statement for statement,_ in statements)


def test_integrity_preupgrade_validation_retry_keeps_scan_candidates_and_replays_pages(monkeypatch):
    from app import activity_commands
    failed={'id':'scan','kind':'integrity_scan','deduplication_key':'large','generation':7,
            'priority':40,'state':'failed','phase':'validate_cards','attempt_count':5,'max_attempts':5,
            'next_attempt_at':None,'created_at':'2026-10-09T00:00:00+00:00',
            'updated_at':'2026-10-09T00:00:00+00:00','last_error':'interrupted',
            'payload_json':json.dumps({'repertoire_id':'large','graph_generation':7,'after_card_id':'card-099'})}
    statements=[]
    class HistoricalPublisher:
        def execute_native(self,statement,parameters=()):
            statements.append((statement,parameters))
            if statement.startswith('SELECT * FROM background_tasks'): row=failed
            elif 'SELECT scan_generation' in statement: row=('scan:7',)
            else: row=None
            return SimpleNamespace(fetchone=lambda:row)
    monkeypatch.setattr(integrity,'_graph_generation_is_current',lambda *_:True)
    monkeypatch.setattr(integrity,'integrity_publication_is_complete',lambda *_:False)
    activity_commands.retry_failed_task(HistoricalPublisher(),{'task_id':'scan'})
    update=next(parameters for statement,parameters in statements if statement.startswith('UPDATE background_tasks SET state'))
    assert update[0]=='publish'
    assert all('DELETE ' not in statement for statement,_ in statements)
