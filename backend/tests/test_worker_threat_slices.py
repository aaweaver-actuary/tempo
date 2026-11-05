"""Retained defensive producer slices load only after the delivery fence."""

import pytest
from test_worker_legacy_evidence_registration import SETUP, run_fresh


@pytest.mark.parametrize(
    "kind",
    [
        "defensive_threat_report_audit",
        "defensive_threat_scan",
        "defensive_threat_backfill",
        "defensive_threat_validate",
    ],
)
def test_retained_threat_slice_calls_actual_owner_after_original_fence(kind):
    run_fresh(
        SETUP
        + f"""
from app.services import threat_pipeline as owner
tasks.defer_paused_defensive_task = lambda _: False
owner.activity_gate.wait_for_foreground = lambda: None
owner.background_read_connection = owner.read_connection = lambda: nullcontext(object())
owner.connection = lambda **_: nullcontext(object())
owner.postgres_store.configured = lambda: True
owner.storage.next_saved_report = lambda *_: None
owner.storage.next_analyzed_game = lambda *_: None
owner.storage.scan_game = lambda *_: None
owner.storage.scan_candidate_lines = lambda *_: []
owner.storage.validation_candidate = lambda *_: None
owner.storage.validation_reports = lambda *_: []
locks = []
owner.lock_current_slice = lambda db, task: locks.append(task) or False
claimed = {{'kind':{kind!r},'id':'retained','generation':8,'lease_token':'original-lease',
    'payload':{{'game_id':'game','analysis_version':1,'cursor':0,'candidate_id':'candidate'}}}}
events = []
completed = []
tasks.complete_task = lambda *args, **kwargs: completed.append((args, kwargs))
tasks.current_delivery = lambda task: events.append(task) or True
assert tasks._execute_claimed_background_slice(claimed,None) is False
assert events == [claimed] and events[0] is claimed
if {kind!r} in ('defensive_threat_report_audit','defensive_threat_scan'):
    assert completed == [(('retained',8,'original-lease'), {{'kind':{kind!r}}})]
else:
    assert completed == []
if {kind!r} in ('defensive_threat_backfill','defensive_threat_validate'):
    assert locks == [claimed] and locks[0] is claimed
"""
    )


def test_stale_threat_work_does_not_import_and_missing_owner_keeps_failure_identity():
    run_fresh(
        """
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.services.threat_pipeline': raise ImportError('missing threat producer')
sys.meta_path.insert(0,Deny())
"""
        + SETUP
        + """
claimed={'kind':'defensive_threat_scan','id':'retained','generation':8,'lease_token':'original-lease','payload':{}}
tasks.defer_paused_defensive_task=lambda _:False
events=[]
tasks.current_delivery=lambda task: events.append(('fence',task)) or False
tasks.record_stale_delivery=lambda task: events.append(('stale',task))
assert tasks._execute_claimed_background_slice(claimed,None) is False
assert [event[0] for event in events] == ['fence','stale']
assert 'app.services.threat_pipeline' not in sys.modules
events.clear()
tasks.current_delivery=lambda task: events.append(('fence',task)) or True
tasks.fail_task=lambda ident,generation,token,error: events.append(('failed',ident,generation,token,str(error)))
try: tasks._execute_claimed_background_slice(claimed,None)
except ImportError as error: assert str(error)=='missing threat producer'
else: raise AssertionError('missing owner hidden')
assert events == [('fence',claimed),('failed','retained',8,'original-lease','missing threat producer')]
"""
    )
