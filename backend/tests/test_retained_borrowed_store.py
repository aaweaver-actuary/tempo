"""The retained factory borrows the caller connection without taking ownership."""
from contextlib import contextmanager
from types import SimpleNamespace


def test_retained_factory_uses_same_wrapper_and_native_transaction(monkeypatch):
    from app.repertoire.changes import retained
    events = []

    class Native:
        @contextmanager
        def transaction(self):
            events.append('savepoint enter')
            try:
                yield
            finally:
                events.append('savepoint exit')

        def execute(self, statement, parameters=()):
            events.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: ('rep',))

        def commit(self):
            raise AssertionError('independent commit')

        def close(self):
            raise AssertionError('independent close')

    native = Native()
    wrapper = SimpleNamespace(raw=native)
    monkeypatch.setattr(retained, 'lock_retained_sources', lambda db, rep: events.append(('lock', db, rep)))
    monkeypatch.setattr(retained, 'current_card_definitions', lambda db, rep: (db, rep))
    monkeypatch.setattr(retained, 'publish_validated_change', lambda db, change, **kw: (db, change, kw))
    reader = lambda db, rep: (db, rep)
    owner = retained.retained_changes(wrapper, 'rep', current_snapshot=reader)
    store = owner.store
    assert store.connection is native
    with store.transaction('rep') as tx:
        assert tx.snapshot() == (wrapper, 'rep')
        assert tx.card_definitions() == (wrapper, 'rep')
        assert store.publisher(native, 'change')[0:2] == (wrapper, 'change')
    assert events == ['savepoint enter', ("SET LOCAL lock_timeout = '2s'", ()),
                      ("SET LOCAL statement_timeout = '5s'", ()), ('lock', wrapper, 'rep'),
                      ('SELECT id FROM repertoires WHERE id=%s FOR UPDATE', ('rep',)), 'savepoint exit']
