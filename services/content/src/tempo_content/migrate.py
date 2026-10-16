"""Explicit Content-owned migration authority and runtime compatibility checks."""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql


# One ownership contract drives both owner grants and runtime verification.
RUNTIME_TABLE_PRIVILEGES = {
    **{table: (('SELECT',), ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'))
       for table in ('migration_history','instance')},
    **{table: (('SELECT','INSERT'), ('UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'))
       for table in ('input_items','input_manifests','source_revisions','exercise_identities','exercise_versions','publications')},
    **{table: (('SELECT','INSERT','UPDATE','DELETE'), ())
       for table in ('sources','tasks','artifacts','generation_routes','positions','legal_replies','occurrences','generation_exercises','responses','collector')},
}


def expected_migrations():
    directory = Path(__file__).parent / 'migrations'
    manifest = json.loads((directory / 'manifest.json').read_text())
    expected = {}
    for entry in manifest:
        if entry['id'] in expected or not set(entry['depends_on']) <= expected.keys():
            raise ValueError('Invalid Content migration order')
        if Path(entry['path']).name != entry['path']:
            raise ValueError('Invalid Content migration path')
        expected[entry['id']] = hashlib.sha256((directory / entry['path']).read_bytes()).hexdigest()
    return expected


def migrate(owner_url, workspace_id, runtime_role):
    directory = Path(__file__).parent / 'migrations'
    expected = expected_migrations()
    with psycopg.connect(owner_url) as database:
        database.execute("SELECT pg_advisory_xact_lock(hashtextextended('content:migrations',0))")
        role = database.execute('SELECT current_user').fetchone()[0]
        if role == runtime_role:
            raise ValueError('Owner migration credential required')
        exists = database.execute("SELECT to_regclass('content.migration_history')").fetchone()[0]
        applied = dict(database.execute('SELECT id,checksum FROM content.migration_history').fetchall()) if exists else {}
        if set(applied) - expected.keys():
            raise ValueError('Unknown Content migration')
        for entry in json.loads((directory / 'manifest.json').read_text()):
            identity = entry['id']
            if identity in applied:
                if applied[identity] != expected[identity]:
                    raise ValueError('Applied Content migration checksum changed')
            else:
                database.execute((directory / entry['path']).read_text(), prepare=False)
                database.execute('INSERT INTO content.migration_history VALUES(%s,%s)', (identity,expected[identity]))
        database.execute('INSERT INTO content.instance VALUES(1,%s) ON CONFLICT DO NOTHING',(workspace_id,))
        if database.execute('SELECT workspace_id FROM content.instance WHERE id=1').fetchone()[0] != workspace_id:
            raise ValueError('Cannot rebind Content workspace')
        database.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        database.execute('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA content FROM PUBLIC')
        database.execute(sql.SQL('GRANT USAGE ON SCHEMA content TO {}').format(sql.Identifier(runtime_role)))
        for table,(required,forbidden) in RUNTIME_TABLE_PRIVILEGES.items():
            if forbidden:
                database.execute(sql.SQL('REVOKE ALL ON content.{} FROM {}').format(
                    sql.Identifier(table),sql.Identifier(runtime_role)))
            database.execute(sql.SQL('GRANT {} ON content.{} TO {}').format(
                sql.SQL(',').join(map(sql.SQL,required)),sql.Identifier(table),sql.Identifier(runtime_role)))


def readiness(database, workspace_id):
    role = database.execute('SELECT current_user').fetchone()[0]
    privileges = database.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
    if any(privileges):
        raise ValueError('Content runtime role is privileged')
    if not database.execute("SELECT has_schema_privilege(current_user,'content','USAGE')").fetchone()[0]:
        raise ValueError('Content runtime missing content schema USAGE')
    for table,(required,forbidden) in RUNTIME_TABLE_PRIVILEGES.items():
        for privilege in required:
            if not database.execute('SELECT has_table_privilege(current_user,%s,%s)',('content.'+table,privilege)).fetchone()[0]:
                raise ValueError('Content runtime missing content.'+table+' '+privilege)
        if forbidden and database.execute('SELECT has_table_privilege(current_user,%s,%s)',('content.'+table,','.join(forbidden))).fetchone()[0]:
            raise ValueError('Content runtime has owner privileges on content.'+table)
    if dict(database.execute('SELECT id,checksum FROM content.migration_history').fetchall()) != expected_migrations():
        raise ValueError('Content schema incompatible')
    if database.execute('SELECT workspace_id FROM content.instance WHERE id=1').fetchone()[0] != workspace_id:
        raise ValueError('Content workspace mismatch')
    if database.execute("SELECT has_schema_privilege(current_user,'content','CREATE')").fetchone()[0]:
        raise ValueError('Content runtime can change schema')
    if database.execute("SELECT pg_has_role(current_user,nspowner,'MEMBER') FROM pg_namespace WHERE nspname='content'").fetchone()[0]:
        raise ValueError('Content runtime can assume schema owner role')
    if database.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE,TEMPORARY')").fetchone()[0]:
        raise ValueError('Content runtime has database creation privileges')
    return {'status':'ready','workspace_id':workspace_id,'runtime_role':role,'migrations':list(expected_migrations())}
