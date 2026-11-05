"""Coverage producer implementations are selected only for eligible work."""

import pytest
from test_worker_legacy_evidence_registration import SETUP, run_fresh


@pytest.mark.parametrize(
    ("kind", "module"),
    [
        ("coverage_seed", "postgres_coverage_seed"),
        ("coverage_explorer", "postgres_coverage_explorer"),
    ],
)
def test_stale_coverage_slice_skips_import_and_missing_owner_keeps_failure_identity(
    kind, module
):
    run_fresh(
        f"""
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.services.{module}': raise ImportError('missing coverage owner')
sys.meta_path.insert(0,Deny())
"""
        + SETUP
        + f"""
tasks.defer_paused_defensive_task = lambda _: False
claimed = {{'kind':{kind!r},'id':'retained','generation':7,'lease_token':'original-lease','payload':{{}}}}
events = []
tasks.current_delivery = lambda value: events.append(('fence',value)) or False
tasks.record_stale_delivery = lambda value: events.append(('stale',value))
assert tasks._execute_claimed_background_slice(claimed,None) is False
assert [item[0] for item in events] == ['fence','stale']
assert 'app.services.{module}' not in sys.modules
events.clear()
tasks.current_delivery = lambda value: events.append(('fence',value)) or True
tasks.fail_task = lambda ident,generation,token,error: events.append(('failed',ident,generation,token,str(error)))
try: tasks._execute_claimed_background_slice(claimed,None)
except ImportError as error: assert str(error) == 'missing coverage owner'
else: raise AssertionError('missing coverage owner hidden')
assert events == [('fence',claimed),('failed','retained',7,'original-lease','missing coverage owner')]
"""
    )


def test_disabled_coverage_recovery_does_not_import_owner():
    run_fresh(
        """
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.services.postgres_coverage_recovery': raise AssertionError(fullname)
sys.meta_path.insert(0,Deny())
"""
        + SETUP
        + """
tasks.study_only_enabled = lambda: True
assert tasks.recover_active_coverage.run() is False
assert 'app.services.postgres_coverage_recovery' not in sys.modules
"""
    )
