"""Compare complete SQLite effects against the preserved source-main wrapper."""
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import pytest

checkout = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
sys.path[:0] = [str(checkout/'backend'),str(checkout/'backend/tests')]
from app.services import review_service
from app.services.scheduler import schedule_review
from test_scheduling_review_store import scheduling_database
spec = importlib.util.spec_from_file_location('app.services._a1_baseline',evidence/'original-review-service.py')
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
original.unlock_legacy_children_after_review = lambda *args: None
fixed_fsrs = schedule_review('correct',reviewed_at=datetime(2026,1,1,tzinfo=timezone.utc),review_day=date(2026,1,1)).fsrs_card_json
count = 0
for mode in ('normal','light','hard'):
    for outcome in ('correct','again'):
        for guided in (False,True):
            for seeded in (False,True):
                captures = []
                for implementation in (original,review_service):
                    with pytest.MonkeyPatch.context() as monkeypatch:
                        fixture = scheduling_database.__wrapped__(monkeypatch)
                        connection = next(fixture)
                        try:
                            connection.execute('UPDATE cards SET scheduling_mode=?,fsrs_card_json=?,interval_days=3,hard_correct_streak=1',(mode,fixed_fsrs))
                            if seeded:
                                connection.execute('INSERT INTO opening_card_schedule_seeds VALUES(?,?,?)',('card',2,1))
                                connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,invalidated_at) VALUES('card','again','2026-06-01T12:00:00','retired')")
                            result = implementation.apply_scheduling_review(connection,'card',outcome,guided=guided,
                                source_kind='study',source_ref='original',light_first_interval_days=7,
                                reviewed_at=datetime(2026,6,2,2,30,tzinfo=timezone.utc),review_day=date(2026,6,1))
                            captures.append({'result':result,'cards':[dict(row) for row in connection.execute('SELECT * FROM cards')],
                                             'history':[dict(row) for row in connection.execute('SELECT * FROM reviews ORDER BY id')]})
                        finally:
                            fixture.close()
                assert captures[0] == captures[1], (mode,outcome,guided,seeded,captures)
                count += 1
print(json.dumps({'source_sha':'3f7332399796366eb32f9fcb5e1b5c678a5b1562','candidate_sha':'8200e096d6a2b3475dba34f55c7ff381785be0a0','cases':count,'comparison':'complete result, card columns including FSRS JSON, and history rows','status':'passed'}))
