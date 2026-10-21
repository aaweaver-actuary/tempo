"""Deadline-bounded driver wait using a fake base wait and no native connection."""
import asyncio

from psycopg import Connection, OperationalError
import pytest

from tempo_intelligence import runtime
from tempo_intelligence_database.adapters import background_connection, store
from tempo_intelligence_database.adapters.background_connection import BackgroundConnection


@pytest.mark.parametrize('case',['unbounded','remaining','caller-limit','expired','wait-failure'])
def test_background_wait_preserves_deadline_bounds_close_and_public_errors(monkeypatch,case):
    events=[];generator=object()
    class LocalConnection(BackgroundConnection):
        # The real driver's initializer/finalizer and native connection are unused.
        def __init__(self):self.was_closed=False
        def __del__(self):pass
        def close(self):
            self.was_closed=True
            events.append('close')
    connection=LocalConnection()
    timeout=2 if case=='caller-limit' else None
    def clock():
        events.append('clock')
        return 40.0
    def wait(owner,received,*,interval,timeout):
        assert owner is connection and received is generator and interval==0.25
        assert timeout==(None if case=='unbounded' else 2 if case=='caller-limit' else 5)
        events.append('wait')
        if case=='wait-failure':raise OperationalError('synthetic-private-driver-detail')
        return 'completed'
    monkeypatch.setattr(background_connection.time,'monotonic',clock)
    monkeypatch.setattr(Connection,'wait',wait)
    token=runtime.background_deadline.set(None if case=='unbounded' else 40 if case=='expired' else 45)
    try:
        if case in ('expired','wait-failure'):
            message='background_database_deadline' if case=='expired' else 'background_database_unavailable'
            with pytest.raises(OperationalError,match='^'+message+'$') as raised:
                connection.wait(generator,interval=0.25,timeout=timeout)
            assert raised.value.__cause__ is None and raised.value.__suppress_context__
            assert connection.was_closed and events[-1]=='close'
        else:
            assert connection.wait(generator,interval=0.25,timeout=timeout)=='completed'
            assert not connection.was_closed
        assert events==(['wait'] if case=='unbounded' else ['clock','close'] if case=='expired' else ['clock','wait','close'] if case=='wait-failure' else ['clock','wait'])
    finally:runtime.background_deadline.reset(token)


def test_runner_store_and_driver_share_one_deadline_context_and_reset_after_operation():
    assert store.BackgroundConnection is BackgroundConnection
    assert background_connection.background_deadline is runtime.background_deadline is store.background_deadline
    async def scenario():
        runner=runtime.DatabaseRunner()
        try:
            def observe():
                value=runtime.background_deadline.get()
                assert value is not None
                assert value==background_connection.background_deadline.get()==store.background_deadline.get()
                return value
            assert await runner.run(observe) is not None
            assert runtime.background_deadline.get() is None
            observed=await asyncio.get_running_loop().run_in_executor(runner.executor,runtime.background_deadline.get)
            assert observed is None
        finally:await runner.aclose()
    asyncio.run(scenario())
