"""Portable application behavior over the same borrowed storage operations."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services import study_portable as application
from tempo_legacy_database.adapters.studies.portable import storage


class Cursor:
    def __init__(self, value=None, rows=()):
        self.value, self.rows = value, rows
    def fetchone(self):
        return self.value
    def __iter__(self):
        return iter(self.rows)


class Borrowed:
    def __init__(self, replies):
        self.replies, self.calls = iter(replies), []
    def execute(self, statement, parameters=()):
        self.calls.append(('sqlite', parameters))
        return next(self.replies)
    def execute_native(self, statement, parameters=()):
        self.calls.append(('postgres', parameters))
        return next(self.replies)
    def __getattr__(self, name):
        raise AssertionError('Unexpected transaction/lifecycle access: ' + name)


class PostgresBorrowed(Borrowed, storage.PostgresConnection):
    """Exercise the real isinstance branch without constructing a driver client."""


def bundle():
    return dict(format='tempo-study', schema_version=1, tables=dict(
        studies=[dict(id='study', title='Title')],
        study_chapters=[dict(id='chapter', study_id='study')],
        study_sources=[dict(id='source', chapter_id='chapter', source_group_id='group')],
        study_positions=[dict(id='position', source_id='source', parent_id=None,
                              fen='8/8/8/8/8/8/4K3/7k w - - 0 1')],
        study_exercises=[], study_exercise_revisions=[],
        study_links=[dict(id='link', study_id='study', source_position_id='position',
                          target_position_id='position', target_exercise_id=None)]))


def column_replies(tables):
    return [Cursor(rows=[dict(name=key) for key in tables[name][0]]) if tables[name] else Cursor()
            for name in application.TABLES]


def test_portable_missing_export_header_rejects_before_dependent_reads():
    database = Borrowed([Cursor()])
    with pytest.raises(KeyError, match='Study not found'):
        application.export_bundle(database, 'missing')
    assert database.calls == [('sqlite', ('missing',))]


def test_portable_copy_validates_columns_then_remaps_ids_before_ordered_borrowed_inserts(monkeypatch):
    content = bundle()
    original = deepcopy(content)
    tables = content['tables']
    database = Borrowed(column_replies(tables) + [Cursor()] + [Cursor()] * 5)
    identifiers = iter(['new-study', 'new-chapter', 'new-source', 'new-position', 'new-link', 'new-group'])
    uuid_calls = []
    def new_id():
        uuid_calls.append(len(database.calls))
        return next(identifiers)
    monkeypatch.setattr(application, 'uuid', SimpleNamespace(uuid4=new_id))
    assert application.import_bundle(database, content, copy=True) == {'study_id': 'new-study', 'idempotent': False}
    assert content == original and uuid_calls == [7] * 6
    assert database.calls[7] == ('sqlite', ('new-study',))
    assert [parameters for _, parameters in database.calls[8:]] == [
        ('new-study', 'Title'), ('new-chapter', 'new-study'),
        ('new-source', 'new-chapter', 'new-group'),
        ('new-position', 'new-source', None, tables['study_positions'][0]['fen']),
        ('new-link', 'new-study', 'new-position', 'new-position', None)]


@pytest.mark.parametrize('conflicting', [False, True])
def test_portable_replay_ignores_row_order_but_preserves_content_conflict_without_writes(conflicting):
    content = bundle()
    content['tables']['study_chapters'].append(dict(id='other-chapter', study_id='study'))
    tables = content['tables']
    exported = deepcopy(tables)
    exported['study_chapters'].reverse()
    if conflicting:
        exported['studies'][0]['title'] = 'Changed'
    export_replies = [Cursor(value=exported['studies'][0])]
    export_replies += [Cursor(rows=exported[name]) for name in application.TABLES[1:]]
    database = Borrowed(column_replies(tables) + [Cursor(value=(1,))] + export_replies)
    if conflicting:
        with pytest.raises(ValueError, match='different content; import as a copy'):
            application.import_bundle(database, content)
    else:
        assert application.import_bundle(database, content) == {'study_id': 'study', 'idempotent': True}
    assert len(database.calls) == 15


@pytest.mark.parametrize('postgres', [False, True])
def test_portable_column_discovery_preserves_dialect_and_rejects_before_uuid_or_write(monkeypatch, postgres):
    cls = PostgresBorrowed if postgres else Borrowed
    database = cls([Cursor(rows=[{'column_name' if postgres else 'name': 'invalid'}])])
    def forbidden():
        raise AssertionError('Column rejection reached UUID mapping')
    monkeypatch.setattr(application, 'uuid', SimpleNamespace(uuid4=forbidden))
    with pytest.raises(ValueError, match='Invalid studies columns'):
        application.import_bundle(database, bundle(), copy=True)
    assert database.calls == ([('postgres', ('studies',))] if postgres else [('sqlite', ())])
    assert application.PostgresConnection is storage.PostgresConnection
