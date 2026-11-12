"""Finite native prefix-split acceptance/replay proof, never HTTP acceptance."""
import json
import os
import sys
from urllib.parse import urlparse

from tempo_storage import postgres
from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION
from app import prefix_split_commands  # Register the actual production command.
from app.command_gateway import execute_command, read_operation, request_digest
from app.services.prefix_split import preview_prefix_split

assert os.environ.get('TEMPO_TEST_INSTANCE') == 'disposable'
address = urlparse(os.environ['TEMPO_DATABASE_WRITE_URL'])
assert (address.hostname, address.port, address.path, address.username) == ('postgres', 5432, '/tempo', 'tempo_writer')
card_id, repertoire_id, operation_id, revision_text = sys.argv[1:]
assert operation_id.startswith('pg-study-prefix-split-')
revision = int(revision_text)
with postgres.connection(read_only=True) as database:
    identity = database.execute_native("SELECT current_database(),current_user,shobj_description((SELECT oid FROM pg_database WHERE datname=current_database()),'pg_database')").fetchone()
    assert tuple(identity) == ('tempo','tempo_writer','tempo-disposable-postgres-test')
    versions = [row[0] for row in database.execute_native('SELECT version FROM tempo_schema_migrations ORDER BY version')]
    assert versions == list(range(1,POSTGRES_SCHEMA_VERSION+1))
    existing = database.execute_native('SELECT shortened_card_id FROM prefix_splits WHERE source_card_id=%s', (card_id,)).fetchone()
    membership_card = existing[0] if existing else card_id
    assert database.execute_native('SELECT 1 FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s', (repertoire_id,membership_card)).fetchone()
    if not existing:
        assert preview_prefix_split(database,card_id)['source_revision'] == revision
payload = {'card_id':card_id,'request':{'expected_revision':revision}}
command = 'cards.prefix_split.accept'
result = execute_command(operation_id,command,payload)
assert result['applied'] is True
receipt = read_operation(operation_id,command_name=command,request_hash=request_digest(command,payload))
assert receipt['state'] == 'complete' and receipt['response'] == result
with postgres.connection(read_only=True) as database:
    before = [dict(row) for row in database.execute_native('SELECT * FROM prefix_splits WHERE source_card_id=%s ORDER BY source_card_id',(card_id,))]
assert len(before) == 1
assert execute_command(operation_id,command,payload) == result
with postgres.connection(read_only=True) as database:
    after = [dict(row) for row in database.execute_native('SELECT * FROM prefix_splits WHERE source_card_id=%s ORDER BY source_card_id',(card_id,))]
assert after == before
print(json.dumps({'scope':'native-command-durability-not-HTTP','unresolved_transport':'prefix-split HTTP GET/POST unmounted','operation_id':operation_id,'response':result,'split_rows':after}))
