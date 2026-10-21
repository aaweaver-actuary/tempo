"""Authored Study command storage and the retained caller-owned boundaries."""
import ast
from pathlib import Path
import sqlite3

import pytest
from fastapi import HTTPException

from app import study_commands


class BorrowedSqlite:
    def __init__(self, database):
        self.database = database

    def execute(self, statement, parameters=()):
        return self.database.execute(statement.replace(' FOR UPDATE', ''), parameters)


@pytest.fixture
def authored_store():
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE studies(id TEXT PRIMARY KEY,title TEXT,description TEXT,
                source_json TEXT,created_at TEXT,updated_at TEXT,archived INTEGER DEFAULT 0);
            CREATE TABLE study_chapters(id TEXT PRIMARY KEY,study_id TEXT,title TEXT,
                description TEXT,position INTEGER,UNIQUE(study_id,position));
            CREATE TABLE study_sources(id TEXT PRIMARY KEY,chapter_id TEXT);
            CREATE TABLE study_positions(id TEXT PRIMARY KEY,source_id TEXT);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT);
            CREATE TABLE study_links(id TEXT PRIMARY KEY,study_id TEXT,source_position_id TEXT,
                target_position_id TEXT,target_exercise_id TEXT,relation TEXT);
        ''')
        yield database, BorrowedSqlite(database)


def test_chapter_commands_retain_two_pass_order_and_callers_savepoint(authored_store):
    database, borrowed = authored_store
    study = study_commands.create_study(borrowed, {'title': 'Original', 'source': {'z': 'last', 'a': 'first'}})['id']
    study_commands.update_study(borrowed, {'study_id': study, 'study': {'title': 'Updated'}})
    assert database.execute('SELECT title FROM studies').fetchone()[0] == 'Updated'
    first = study_commands.create_chapter(borrowed, {'study_id': study, 'chapter': {'title': 'First'}})['id']
    second = study_commands.create_chapter(borrowed, {'study_id': study, 'chapter': {'title': 'Second'}})['id']
    study_commands.rename_chapter(borrowed, {'study_id': study, 'chapter_id': second,
                                           'chapter': {'title': 'Renamed'}})
    # The real unique constraint requires the original negative-position pass.
    assert study_commands.reorder_chapters(borrowed, {'study_id': study, 'chapter_ids': [second, first]}) == {'chapter_ids': [second, first]}
    order = lambda: [row[0] for row in database.execute('SELECT id FROM study_chapters ORDER BY position')]
    assert order() == [second, first]
    database.execute('SAVEPOINT caller')
    study_commands.reorder_chapters(borrowed, {'study_id': study, 'chapter_ids': [first, second]})
    database.execute('ROLLBACK TO SAVEPOINT caller')
    assert order() == [second, first]
    with pytest.raises(HTTPException) as error:
        study_commands.reorder_chapters(borrowed, {'study_id': study, 'chapter_ids': [first, first]})
    assert error.value.status_code == 422 and order() == [second, first]


def test_link_checks_same_study_before_writing(authored_store):
    database, borrowed = authored_store
    database.executescript('''
        INSERT INTO studies(id) VALUES('study'),('other');
        INSERT INTO study_chapters(id,study_id,position) VALUES('chapter','study',0),('other','other',0);
        INSERT INTO study_sources VALUES('source','chapter'),('other','other');
        INSERT INTO study_positions VALUES('from','source'),('to','source'),('outside','other');
    ''')
    payload = {'study_id': 'study', 'link': {'source_position_id': 'from',
               'target_position_id': 'to', 'relation': 'illustrates'}}
    link = study_commands.create_link(borrowed, payload)
    assert tuple(database.execute('SELECT id,source_position_id,target_position_id FROM study_links').fetchone()) == (link['id'], 'from', 'to')
    with pytest.raises(HTTPException) as error:
        study_commands.create_link(borrowed, {**payload, 'link': {**payload['link'], 'target_position_id': 'outside'}})
    assert error.value.status_code == 422
    assert database.execute('SELECT COUNT(*) FROM study_links').fetchone()[0] == 1


def test_existing_record_allowlist_rejects_unknown_table_before_database_access():
    class NoDatabaseAccess:
        def execute(self, *_):
            pytest.fail('Unknown table reached a database query')

    with pytest.raises(AssertionError, match='Unknown study table'):
        study_commands._require_record(NoDatabaseAccess(), 'unrelated_table', 'id')


def test_authored_command_policy_keeps_sql_and_driver_operations_in_root_database():
    for node in ast.walk(ast.parse(Path(study_commands.__file__).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {'execute', 'execute_native', 'executemany', 'commit', 'rollback'}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not any(word in node.value.upper() for word in ('SELECT ', 'INSERT INTO ', 'UPDATE STUDY_', 'UPDATE CARDS ', 'DELETE FROM '))
