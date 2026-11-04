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
        with pytest.raises(TypeError): ContentFinalReservationHandle()
        assert producer.execute(ReserveFinal('transfer-a',draft['source_id'],publication['publication_id'],2,operation['id'])).code == 'content_final_request_conflict'
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
    with ContentService(url,workspace) as restarted:
        restored = make_final_generation(restarted)
        assert restored.execute(CaptureFinal(reserved.handle)).code == 'content_final_handle_invalid'
        recovered = restored.execute(RecoverFinal('transfer-a',draft['source_id']))
        assert recovered.capture == evidence and recovered.epoch == 1
        unknown = restored.execute(ResolveFinal(recovered.handle,'accept'))
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
