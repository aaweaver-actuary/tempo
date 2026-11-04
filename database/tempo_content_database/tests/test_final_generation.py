"""Native C-FENCE producer checks against the Content-owned disposable runner."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import hashlib
import json
import os
import uuid

import chess
import psycopg
import pytest

from tempo_content.bridge import Bridge
from tempo_content.domain import ContentError
from tempo_content.final_generation import (ReserveFinal, CaptureFinal, RecoverFinal, ResolveFinal,
    AcceptedTarget, ProvenNoTargetWrite, ContentFinalReservationHandle)
from tempo_content.generation_envelope import validate_content_generation_envelope
from tempo_content_database.adapters.final_generation import make_final_generation
from tempo_content_database.adapters.store import ContentService


def _source(workspace):
    return {'schema_version':1, 'workspace_id':workspace, 'source_key':'final-'+uuid.uuid4().hex, 'title':'Final fixture',
        'repertoires':[{'key':'one','title':'One','learner_color':'white'},{'key':'two','title':'Two','learner_color':'white'}],
        'policies':[{'key':'one','repertoire_key':'one'},{'key':'two','repertoire_key':'two'}],
        'routes':[{'key':'route','starting_fen':chess.STARTING_FEN,'moves':['e2e4','e7e5']}],
        'memberships':[{'route_key':'route','policy_key':'one','repertoire_key':'one'},{'route_key':'route','policy_key':'two','repertoire_key':'two'}],
        'exercises':[{'key':key,'decision_key':key,'policy_key':key,'starting_fen':chess.STARTING_FEN,'prompt':'Play e4',
                      'specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[['e2e4']]}} for key in ('one','two')],
        'bindings':[{'route_key':'route','policy_key':key,'ply':0,'exercise_key':key} for key in ('one','two')]}


class Reader:
    def __init__(self, disposition): self.disposition = disposition
    def read_content_transfer(self, transfer_id, certificate_digest): return self.disposition


def test_content_final_generation_cf01_cf07_supported_writer_and_restart():
    assert os.environ.get('TEMPO_CONTENT_TEST_CONTAINER','').startswith('tempo-content-')
    url, workspace = os.environ['TEMPO_CONTENT_DATABASE_URL'], os.environ['TEMPO_CONTENT_WORKSPACE']
    source = _source(workspace)
    manifest = {'schema_version':1,'kind':'authored_import','workspace_id':workspace,'source_key':source['source_key'],
        'title':source['title'],'complete':True,'export_profile':'learning','legacy_aliases':{'one':'legacy-one','two':'legacy-two'},
        'provenance':{'kind':'synthetic','source_sha256':hashlib.sha256(json.dumps(source).encode()).hexdigest(),'description':'native synthetic'}}
    with ContentService(url,workspace) as service:
        bridge = Bridge(service)
        draft = bridge.draft(source,manifest,0)
        operation = bridge.prepare(bridge.approve(draft['id'],draft['digest'])['id'],100)
        publication = operation['document']['export']['publication']
        bridge.record(operation['id'], {'transport':'authenticated_learning_http','endpoint':'synthetic-owner-test', 'acknowledgement':{
            'contract_version':1,'workspace_id':workspace,'activation_id':operation['id']+':activate',
            'publication_id':publication['publication_id'],'publication_generation':publication['publication_generation'],
            'activation_revision':1,'status':'activated','reason':None}})
        command = ReserveFinal('transfer-a',draft['source_id'],publication['publication_id'],publication['publication_generation'],operation['id'])
        producer = make_final_generation(service)
        competing_manifest = deepcopy(manifest)
        competing_manifest['provenance']['description'] = 'competing approval'
        competing = bridge.draft(source,competing_manifest,1)
        barrier = Barrier(2)
        before_commit, release_commit = Event(), Event()
        original_commit = service._commit_publication
        def delayed_commit(*args, **kwargs):
            before_commit.set()
            assert release_commit.wait(15)
            return original_commit(*args, **kwargs)
        service._commit_publication = delayed_commit
        with psycopg.connect(url) as db:
            publication_provenance = db.execute('SELECT provenance FROM content.publications WHERE publication_id=%s AND generation=%s', (publication['publication_id'],publication['publication_generation'])).fetchone()[0]
        def reserve_race():
            barrier.wait(timeout=5)
            return producer.execute(command)
        def approve_race():
            barrier.wait(timeout=5)
            try:
                return bridge.approve(competing['id'],competing['digest'])
            except ContentError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=3) as executor:
            stale_publication = executor.submit(service.store_publication, publication, draft['source_id'], publication_provenance)
            assert before_commit.wait(10)
            reserve_future = executor.submit(reserve_race)
            approve_future = executor.submit(approve_race)
            reserved = reserve_future.result(timeout=15)
            competing_result = approve_future.result(timeout=15)
            if isinstance(competing_result,dict):
                assert reserved.code == 'content_final_operation_unsettled'
                assert bridge.cancel(competing_result['id'])['cancelled'] is True
                reserved = producer.execute(command)
            else:
                assert competing_result == 'content_source_final_reserved'
            try:
                assert reserved.status == 'RESERVED'
            finally:
                release_commit.set()
            with pytest.raises(ContentError,match='reservation'):
                stale_publication.result(timeout=15)
        service._commit_publication = original_commit
        assert (reserved.status,reserved.epoch) == ('RESERVED',1)
        # Simulate an old worker becoming claimable after the reservation. The
        # real worker path must reject it before acquiring a fresh lease.
        owner_url = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
        with psycopg.connect(owner_url) as db:
            db.execute("UPDATE content.tasks SET state='pending',lease_token='old',lease_until=clock_timestamp()-interval '1 second' WHERE source_id=%s AND generation=%s", (draft['source_id'],publication['publication_generation']))
        try:
            with pytest.raises(ContentError,match='reservation'):
                service.work_once(source['source_key'])
        finally:
            with psycopg.connect(owner_url) as db:
                db.execute("UPDATE content.tasks SET state='complete',lease_token=NULL,lease_until=NULL WHERE source_id=%s AND generation=%s", (draft['source_id'],publication['publication_generation']))
        # Force a disposable owner-only source drift and collect concurrently:
        # the held generation attachments remain, while collector work may run.
        with psycopg.connect(owner_url) as db:
            db.execute('UPDATE content.sources SET requested_revision=2 WHERE id=%s',(draft['source_id'],))
            db.execute('UPDATE content.collector SET pending=true WHERE id=1')
        try:
            service.collect_once()
            with psycopg.connect(url) as db:
                assert db.execute('SELECT count(*) FROM content.generation_routes WHERE source_id=%s AND generation=%s',(draft['source_id'],publication['publication_generation'])).fetchone()[0] == 2
        finally:
            with psycopg.connect(owner_url) as db:
                db.execute('UPDATE content.sources SET requested_revision=1 WHERE id=%s',(draft['source_id'],))
        with pytest.raises(TypeError): ContentFinalReservationHandle()
        conflict = producer.execute(ReserveFinal('transfer-a',draft['source_id'],publication['publication_id'],2,operation['id']))
        assert (conflict.code,conflict.reservation_id,conflict.epoch) == ('content_final_request_conflict',reserved.reservation_id,reserved.epoch)
        captured = producer.execute(CaptureFinal(reserved.handle))
        assert (captured.status,captured.code) == ('CONTENT_CAPTURE_VERIFIED','content_final_captured')
        evidence = captured.capture
        envelope = validate_content_generation_envelope(certificate_json=evidence.certificate_bytes,export_bytes=evidence.export_bytes,expected_transfer_id='transfer-a')
        assert envelope.status == 'ENVELOPE_VALID' and envelope.authority == 'UNVERIFIED'
        export = json.loads(evidence.export_bytes)
        assert len(export['routes']) == 2 and len(export['legacy_aliases']) == 2
        assert len({(r['route_id'],r['policy_id']) for r in export['routes']}) == 2
        with pytest.raises(ContentError,match='reservation'):
            service.load_source(source,0)
        with pytest.raises(ContentError,match='reservation'):
            service.publication_build(source['source_key'])
        assert producer.execute(CaptureFinal(reserved.handle)).capture == evidence
        task = service._one('SELECT * FROM content.tasks WHERE source_id=%s AND generation=%s',(draft['source_id'],publication['publication_generation']))
        with service._transaction() as db:
            with pytest.raises(ContentError,match='reservation'):
                service._fence(db,task)
            with pytest.raises(ContentError,match='source fence'):
                service._advance(db,task)
    with ContentService(url,workspace) as restarted:
        restored = make_final_generation(restarted)
        assert restored.execute(CaptureFinal(reserved.handle)).code == 'content_final_handle_invalid'
        recovered = restored.execute(RecoverFinal('transfer-a',draft['source_id']))
        assert recovered.capture == evidence and recovered.epoch == 1
        # Deliberately bypass the immutable trigger with this disposable owner to
        # demonstrate readback conflict; restore exact original bytes immediately.
        owner_url = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
        with psycopg.connect(owner_url) as db:
            db.execute('ALTER TABLE content.final_generation_captures DISABLE TRIGGER final_capture_immutable')
            db.execute('UPDATE content.final_generation_captures SET export_bytes=%s WHERE capture_id=%s',(evidence.export_bytes+b' ',evidence.capture_id))
            db.execute('ALTER TABLE content.final_generation_captures ENABLE TRIGGER final_capture_immutable')
        try:
            damaged = restored.execute(CaptureFinal(recovered.handle))
            assert damaged.code == 'content_final_immutable_conflict' and damaged.state == 'recovery-held'
        finally:
            with psycopg.connect(owner_url) as db:
                db.execute('ALTER TABLE content.final_generation_captures DISABLE TRIGGER final_capture_immutable')
                db.execute('UPDATE content.final_generation_captures SET export_bytes=%s WHERE capture_id=%s',(evidence.export_bytes,evidence.capture_id))
                db.execute('ALTER TABLE content.final_generation_captures ENABLE TRIGGER final_capture_immutable')
        held_original = restored.execute(RecoverFinal('transfer-a',draft['source_id']))
        assert held_original.state == 'recovery-held' and held_original.handle is not None
        unknown = restored.execute(ResolveFinal(held_original.handle,'accept'))
        assert unknown.code == 'content_final_target_unknown' and unknown.state == 'recovery-held'
        with pytest.raises(ContentError,match='reservation'):
            restarted.load_source(source,0)
        owner = Reader(AcceptedTarget('test-target','fresh-allocation',1,'transfer-a',evidence.certificate_digest,
                                     evidence.export_sha256,'test-legacy-watermark','test-activation-receipt','test-checkpoint','a'*64))
        recovered = make_final_generation(restarted,owner)
        held = recovered.execute(RecoverFinal('transfer-a',draft['source_id']))
        assert held.state == 'recovery-held' and held.handle is not None
        assert recovered.execute(ResolveFinal(held.handle,'accept')).status == 'ACCEPTED'
        assert restarted.load_source(source,0)['replayed'] is True
        # A trusted fake owner port can prove closure before any capture.
        closer = make_final_generation(restarted, Reader(ProvenNoTargetWrite('test-target','closed-allocation','transfer-b','closed-no-write')))
        to_abort = closer.execute(ReserveFinal('transfer-b',draft['source_id'],publication['publication_id'],publication['publication_generation'],operation['id']))
        assert to_abort.status == 'RESERVED' and to_abort.epoch == 2
        assert closer.execute(ResolveFinal(to_abort.handle,'abort')).status == 'ABORTED'
        # A new epoch holds the same source while the native selected-response
        # inventory is deliberately changed with the owner credential.
        next_command = ReserveFinal('transfer-c',draft['source_id'],publication['publication_id'],publication['publication_generation'],operation['id'])
        second = recovered.execute(next_command)
        assert second.status == 'RESERVED' and second.epoch == 3
        owner_url = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
        with psycopg.connect(owner_url) as db:
            db.execute("UPDATE content.responses SET exercise_key='changed' WHERE source_id=%s AND policy_key='one'",(draft['source_id'],))
        try:
            mismatch = recovered.execute(CaptureFinal(second.handle))
            assert mismatch.code == 'content_final_inventory_mismatch' and mismatch.state == 'recovery-held'
        finally:
            with psycopg.connect(owner_url) as db:
                db.execute("UPDATE content.responses SET exercise_key='one' WHERE source_id=%s AND policy_key='one'",(draft['source_id'],))
        assert recovered.execute(ResolveFinal(second.handle,'abort')).code == 'content_final_target_unknown'


def test_content_final_generation_cf02_stale_source_is_not_reserved():
    assert os.environ.get('TEMPO_CONTENT_TEST_CONTAINER','').startswith('tempo-content-')
    url, workspace = os.environ['TEMPO_CONTENT_DATABASE_URL'], os.environ['TEMPO_CONTENT_WORKSPACE']
    source = _source(workspace)
    manifest = {'schema_version':1,'kind':'authored_import','workspace_id':workspace,'source_key':source['source_key'],
        'title':source['title'],'complete':True,'export_profile':'learning',
        'provenance':{'kind':'synthetic','source_sha256':hashlib.sha256(json.dumps(source).encode()).hexdigest(),'description':'stale source test'}}
    with ContentService(url,workspace) as service:
        bridge = Bridge(service)
        draft = bridge.draft(source,manifest,0)
        operation = bridge.prepare(bridge.approve(draft['id'],draft['digest'])['id'],100)
        publication = operation['document']['export']['publication']
        bridge.record(operation['id'], {'transport':'authenticated_learning_http','endpoint':'synthetic-owner-test','acknowledgement':{
            'contract_version':1,'workspace_id':workspace,'activation_id':operation['id']+':activate',
            'publication_id':publication['publication_id'],'publication_generation':publication['publication_generation'],
            'activation_revision':1,'status':'activated','reason':None}})
        # A separately approved default/full manifest cannot be relabelled as learning.
        full_manifest = deepcopy(manifest)
        full_manifest.pop('export_profile')
        full_draft = bridge.draft(source,full_manifest,1)
        full_operation = bridge.prepare(bridge.approve(full_draft['id'],full_draft['digest'])['id'],100)
        bridge.record(full_operation['id'], {'transport':'authenticated_learning_http','endpoint':'synthetic-owner-test','acknowledgement':{
            'contract_version':1,'workspace_id':workspace,'activation_id':full_operation['id']+':activate',
            'publication_id':publication['publication_id'],'publication_generation':publication['publication_generation'],
            'activation_revision':1,'status':'activated','reason':None}})
        full_result = make_final_generation(service).execute(ReserveFinal('full-transfer',draft['source_id'],publication['publication_id'],publication['publication_generation'],full_operation['id']))
        assert full_result.status == 'BLOCK' and full_result.code == 'content_final_profile_not_approved'
        edited = deepcopy(source)
        edited['title'] = 'Changed after warm export'
        assert service.load_source(edited,1)['source_revision'] == 2
        producer = make_final_generation(service)
        result = producer.execute(ReserveFinal('stale-transfer',draft['source_id'],publication['publication_id'],publication['publication_generation'],operation['id']))
        assert result.status == 'BLOCK' and result.code == 'content_final_source_changed'
        with psycopg.connect(url) as db:
            assert db.execute('SELECT count(*) FROM content.final_generation_reservations WHERE source_id=%s',(draft['source_id'],)).fetchone()[0] == 0


def test_content_final_generation_cf06_separate_authority_namespace():
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from tempo_content_database.ddl.migrate import migrate
    assert os.environ.get('TEMPO_CONTENT_TEST_CONTAINER','').startswith('tempo-content-')
    admin_url = os.environ['TEMPO_CONTENT_TEST_ADMIN_URL']
    owner_url = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
    runtime_url = os.environ['TEMPO_CONTENT_DATABASE_URL']
    first_workspace = os.environ['TEMPO_CONTENT_WORKSPACE']
    second_workspace = 'second-'+uuid.uuid4().hex
    database_name = 'content_final_'+uuid.uuid4().hex
    with psycopg.connect(admin_url,autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {} OWNER content_owner').format(sql.Identifier(database_name)))
        db.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database_name)))
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO content_runtime').format(sql.Identifier(database_name)))
    second_owner = make_conninfo(owner_url,dbname=database_name)
    second_runtime = make_conninfo(runtime_url,dbname=database_name)
    migrate(second_owner,second_workspace,'content_runtime')
    with ContentService(runtime_url,first_workspace) as first, ContentService(second_runtime,second_workspace) as second:
        source = _source(first_workspace)
        shared_key = source['source_key']
        first_id = first.load_source(source,0)['source_id']
        source['workspace_id'] = second_workspace
        second_id = second.load_source(source,0)['source_id']
        assert first_id != second_id and source['source_key'] == shared_key
        first_authority = first._one('SELECT database_authority_id FROM content.final_generation_authority WHERE singleton=1')['database_authority_id']
        second_authority = second._one('SELECT database_authority_id FROM content.final_generation_authority WHERE singleton=1')['database_authority_id']
        assert first_authority != second_authority
        mismatch = make_final_generation(second).execute(ReserveFinal('wrong-workspace',first_id,'missing',1,'missing'))
        assert mismatch.status == 'BLOCK' and mismatch.code == 'content_final_authority_mismatch'


def test_content_final_generation_public_import_is_inert():
    import subprocess
    import sys
    code = '''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith(('tempo_content_database','tempo_content.bridge','psycopg')):
   raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
from tempo_content.final_generation import ContentFinalGeneration, ReserveFinal
assert ContentFinalGeneration and ReserveFinal
'''
    result = subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_content_final_generation_terminal_capture_race_never_revives_old_epoch():
    """CF terminal race: a stale capture must not reopen accepted authority."""
    assert os.environ.get('TEMPO_CONTENT_TEST_CONTAINER','').startswith('tempo-content-')
    url, workspace = os.environ['TEMPO_CONTENT_DATABASE_URL'], os.environ['TEMPO_CONTENT_WORKSPACE']
    source = _source(workspace)
    manifest = {'schema_version':1,'kind':'authored_import','workspace_id':workspace,'source_key':source['source_key'],
        'title':source['title'],'complete':True,'export_profile':'learning',
        'provenance':{'kind':'synthetic','source_sha256':hashlib.sha256(json.dumps(source).encode()).hexdigest(),'description':'terminal race'}}
    with ContentService(url,workspace) as initial, ContentService(url,workspace) as first, ContentService(url,workspace) as second:
        bridge = Bridge(initial)
        draft = bridge.draft(source,manifest,0)
        operation = bridge.prepare(bridge.approve(draft['id'],draft['digest'])['id'],100)
        publication = operation['document']['export']['publication']
        bridge.record(operation['id'], {'transport':'authenticated_learning_http','endpoint':'synthetic-owner-test','acknowledgement':{
            'contract_version':1,'workspace_id':workspace,'activation_id':operation['id']+':activate',
            'publication_id':publication['publication_id'],'publication_generation':publication['publication_generation'],
            'activation_revision':1,'status':'activated','reason':None}})
        command = ReserveFinal('terminal-race',draft['source_id'],publication['publication_id'],publication['publication_generation'],operation['id'])
        creator = make_final_generation(initial)
        reservation = creator.execute(command)
        evidence = creator.execute(CaptureFinal(reservation.handle)).capture
        assert evidence is not None
        left, right = make_final_generation(first), make_final_generation(second)
        left_handle = left.execute(RecoverFinal('terminal-race',draft['source_id'])).handle
        right_handle = right.execute(RecoverFinal('terminal-race',draft['source_id'])).handle
        arrived, release_left, release_right = Barrier(3), Event(), Event()
        for producer, release in ((left,release_left),(right,release_right)):
            original = producer._store._read_capture
            def delayed(row, original=original, release=release):
                arrived.wait(timeout=10)
                assert release.wait(15)
                return original(row)
            producer._store._read_capture = delayed
        with ThreadPoolExecutor(max_workers=2) as executor:
            left_future = executor.submit(left.execute,CaptureFinal(left_handle))
            right_future = executor.submit(right.execute,CaptureFinal(right_handle))
            arrived.wait(timeout=10)
            target = Reader(AcceptedTarget('test-target','fresh-allocation',1,'terminal-race',evidence.certificate_digest,
                                           evidence.export_sha256,'legacy-watermark','activation-receipt','checkpoint','a'*64))
            resolver = make_final_generation(initial,target)
            active = resolver.execute(RecoverFinal('terminal-race',draft['source_id']))
            accepted = resolver.execute(ResolveFinal(active.handle,'accept'))
            assert accepted.status == 'ACCEPTED'
            release_left.set()
            first_result = left_future.result(timeout=10)
            if first_result.status != 'BLOCK' or first_result.code != 'content_final_handle_invalid':
                release_right.set()
                pytest.fail(f'terminal reservation returned stale capture: {first_result.status}/{first_result.code}')
            newer = creator.execute(ReserveFinal('next-epoch',draft['source_id'],publication['publication_id'],publication['publication_generation'],operation['id']))
            assert newer.status == 'RESERVED' and newer.epoch == 2
            owner_url = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
            with psycopg.connect(owner_url) as db:
                db.execute('UPDATE content.sources SET requested_revision=2 WHERE id=%s',(draft['source_id'],))
            try:
                release_right.set()
                second_result = right_future.result(timeout=10)
                assert second_result.status == 'BLOCK' and second_result.code == 'content_final_handle_invalid'
            finally:
                with psycopg.connect(owner_url) as db:
                    db.execute('UPDATE content.sources SET requested_revision=1 WHERE id=%s',(draft['source_id'],))
                release_right.set()
            with psycopg.connect(url) as db:
                old = db.execute('SELECT state,capture_id,resolution_json FROM content.final_generation_reservations WHERE reservation_id=%s',(reservation.reservation_id,)).fetchone()
                current = db.execute('SELECT state,epoch FROM content.final_generation_reservations WHERE reservation_id=%s',(newer.reservation_id,)).fetchone()
                stored = db.execute('SELECT certificate_bytes,export_bytes FROM content.final_generation_captures WHERE capture_id=%s',(evidence.capture_id,)).fetchone()
            assert old[0] == 'accepted' and old[1] == evidence.capture_id and old[2]['activation_receipt'] == 'activation-receipt'
            assert current == ('reserved',2)
            assert bytes(stored[0]) == evidence.certificate_bytes and bytes(stored[1]) == evidence.export_bytes
