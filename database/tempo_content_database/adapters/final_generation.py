"""Content-owned final reservation storage and supported-writer guards."""
from tempo_content.domain import ContentError


def assert_source_writable(database, source_id):
    """Caller holds the source row lock (or the source does not yet exist)."""
    active = database.execute("SELECT 1 FROM content.final_generation_reservations WHERE source_id=%s AND state IN ('reserved','captured','recovery-held')", (source_id,)).fetchone()
    if active:
        raise ContentError('content_source_final_reserved', 'Content final generation reservation holds this source')


def lock_writable_source(database, source_id):
    row = database.execute('SELECT * FROM content.sources WHERE id=%s FOR UPDATE', (source_id,)).fetchone()
    if row:
        assert_source_writable(database, source_id)
    return row

# The controller alone invokes these storage methods. Normal writers use the
# guard above and never receive a reservation bypass.
import hashlib
import json
import uuid
from functools import wraps
import psycopg


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _scope(database, source_id):
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('content:scope:' + source_id,))


def _bounded_contention(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled, psycopg.errors.DeadlockDetected) as error:
            raise ContentError('content_final_contention', 'Content final generation transaction could not acquire bounded capacity') from error
    return wrapped


class FinalGenerationStore:
    def __init__(self, service):
        self.service = service

    @_bounded_contention
    def reserve(self, command):
        request = {'transfer_id': command.transfer_id, 'source_id': command.source_id,
                   'publication_id': command.publication_id, 'generation': command.generation,
                   'approved_operation_id': command.approved_operation_id}
        request_digest = _hash(request)
        with self.service._transaction() as db:
            _scope(db, command.source_id)
            source = db.execute('SELECT * FROM content.sources WHERE id=%s FOR UPDATE', (command.source_id,)).fetchone()
            if not source:
                raise ContentError('content_final_source_changed', 'Source is unavailable')
            previous = db.execute('SELECT * FROM content.final_generation_reservations WHERE source_id=%s AND transfer_id=%s FOR UPDATE', (command.source_id, command.transfer_id)).fetchone()
            if previous:
                if previous['request_digest'] != request_digest:
                    raise ContentError('content_final_request_conflict', 'Original transfer request differs')
                return previous
            if db.execute("SELECT 1 FROM content.final_generation_reservations WHERE source_id=%s AND state IN ('reserved','captured','recovery-held')", (command.source_id,)).fetchone():
                raise ContentError('content_source_final_reserved', 'Another final transfer holds this source')
            operation = db.execute('SELECT o.*, d.digest AS draft_digest, d.document AS draft FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.id=%s FOR UPDATE OF o', (command.approved_operation_id,)).fetchone()
            if not operation or operation['source_id'] != command.source_id or operation['state'] != 'completed':
                raise ContentError('content_final_operation_unsettled', 'The original approved operation is not completed')
            if db.execute("SELECT 1 FROM content.bridge_operations WHERE source_id=%s AND state IN ('approved','applying')", (command.source_id,)).fetchone():
                raise ContentError('content_final_operation_unsettled', 'Another approved operation remains unsettled')
            draft = operation['draft']
            if _hash(draft) != operation['draft_digest']:
                raise ContentError('content_final_immutable_conflict', 'Approved draft changed')
            if draft['manifest'].get('export_profile') != 'learning' or draft['manifest'].get('complete') is not True:
                raise ContentError('content_final_profile_not_approved', 'The original approved manifest is not a complete learning profile')
            if draft['source']['workspace_id'] != self.service.workspace_id or draft['source']['source_key'] != source['source_key']:
                raise ContentError('content_final_authority_mismatch', 'Approved source belongs to another authority')
            task = db.execute('SELECT * FROM content.tasks WHERE source_id=%s AND generation=%s FOR UPDATE', (command.source_id, command.generation)).fetchone()
            pub = db.execute('SELECT * FROM content.publications WHERE publication_id=%s AND generation=%s', (command.publication_id, command.generation)).fetchone()
            if not task or task['state'] != 'complete' or not pub:
                raise ContentError('content_final_generation_incomplete', 'Completed task and publication are required')
            if (source['publication_id'] != command.publication_id or source['requested_revision'] != task['revision']
                    or source['last_complete_revision'] != task['revision'] or source['last_complete_generation'] != command.generation
                    or pub['source_id'] != command.source_id or pub['source_revision'] != task['revision']
                    or draft['source'] != pub['provenance']['snapshot']):
                raise ContentError('content_final_source_changed', 'Approved source or generation is no longer current')
            epoch = db.execute('SELECT COALESCE(max(epoch),0)+1 AS epoch FROM content.final_generation_reservations WHERE source_id=%s', (command.source_id,)).fetchone()['epoch']
            reservation_id = str(uuid.uuid4())
            db.execute('UPDATE content.tasks SET lease_token=NULL,lease_until=NULL WHERE source_id=%s AND lease_token IS NOT NULL', (command.source_id,))
            return db.execute("INSERT INTO content.final_generation_reservations(reservation_id,source_id,workspace_id,transfer_id,epoch,publication_id,generation,approved_operation_id,approved_draft_digest,request_digest,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING *", (reservation_id, command.source_id, self.service.workspace_id, command.transfer_id, epoch, command.publication_id, command.generation, command.approved_operation_id, operation['draft_digest'], request_digest)).fetchone()

    @_bounded_contention
    def recover(self, transfer_id, source_id):
        with self.service._transaction() as db:
            _scope(db, source_id)
            db.execute('SELECT id FROM content.sources WHERE id=%s FOR UPDATE', (source_id,)).fetchone()
            return db.execute('SELECT * FROM content.final_generation_reservations WHERE source_id=%s AND transfer_id=%s FOR UPDATE', (source_id, transfer_id)).fetchone()

    @_bounded_contention
    def selected(self, reservation):
        """Read the selected immutable owner records on one repeatable-read snapshot."""
        with self.service._read_snapshot() as db:
            snapshot_id = str(db.execute('SELECT pg_current_snapshot() AS snapshot').fetchone()['snapshot'])
            authority = str(db.execute('SELECT database_authority_id FROM content.final_generation_authority WHERE singleton=1').fetchone()['database_authority_id'])
            schema = db.execute('SELECT id,checksum FROM content.migration_history ORDER BY id').fetchall()
            source = db.execute('SELECT * FROM content.sources WHERE id=%s', (reservation['source_id'],)).fetchone()
            task = db.execute('SELECT * FROM content.tasks WHERE source_id=%s AND generation=%s', (reservation['source_id'], reservation['generation'])).fetchone()
            publication = db.execute('SELECT * FROM content.publications WHERE publication_id=%s AND generation=%s', (reservation['publication_id'], reservation['generation'])).fetchone()
            operation = db.execute('SELECT o.*,d.digest AS draft_digest,d.document AS draft FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.id=%s', (reservation['approved_operation_id'],)).fetchone()
            revision = db.execute('SELECT r.revision,r.digest,r.stage_id,m.header,m.counts,m.source_id AS manifest_source_id,m.digest AS manifest_digest FROM content.source_revisions r JOIN content.input_manifests m ON m.stage_id=r.stage_id WHERE r.source_id=%s AND r.revision=%s', (reservation['source_id'], source['requested_revision'])).fetchone()
            if not revision or revision['manifest_source_id'] != reservation['source_id'] or revision['manifest_digest'] != revision['digest']:
                raise ContentError('content_final_inventory_mismatch', 'Revision manifest identity differs')
            payload = dict(revision['header'])
            prepared_bytes = len(_canonical(payload))
            for kind in ('repertoires', 'policies', 'routes', 'memberships', 'exercises', 'bindings'):
                rows = []
                offset = 0
                while True:
                    page = db.execute('SELECT ordinal,payload FROM content.input_items WHERE stage_id=%s AND kind=%s AND ordinal >= %s ORDER BY ordinal LIMIT 256', (revision['stage_id'], kind, offset)).fetchall()
                    if any(item['ordinal'] != offset + index for index, item in enumerate(page)):
                        raise ContentError('content_final_inventory_mismatch', 'Source item order has a gap')
                    prepared_bytes += sum(len(_canonical(item['payload'])) for item in page)
                    if prepared_bytes > 64*1024*1024:
                        raise ContentError('content_final_capture_limit', 'Selected source exceeds the capture bound')
                    rows.extend(item['payload'] for item in page)
                    offset += len(page)
                    if len(page) < 256:
                        break
                if len(rows) != revision['counts'][kind]:
                    raise ContentError('content_final_inventory_mismatch', 'Immutable source item count differs')
                payload[kind] = rows
            generation = {}
            for table, order in (('generation_routes', 'route_key,policy_key'), ('generation_exercises', 'exercise_key'), ('responses', 'route_key,policy_key,ply')):
                rows, offset = [], 0
                while True:
                    page = db.execute('SELECT * FROM content.' + table + ' WHERE source_id=%s AND generation=%s ORDER BY ' + order + ' LIMIT 256 OFFSET %s', (reservation['source_id'], reservation['generation'], offset)).fetchall()
                    prepared_bytes += sum(len(_canonical(item)) for item in page)
                    if prepared_bytes > 64*1024*1024:
                        raise ContentError('content_final_capture_limit', 'Selected generation exceeds the capture bound')
                    rows.extend(page)
                    offset += len(page)
                    if len(page) < 256:
                        break
                generation[table] = rows
            artifacts = {}
            occurrences = {}
            for row in generation['generation_routes']:
                artifact_id = row['artifact_id']
                if artifact_id not in artifacts:
                    artifacts[artifact_id] = db.execute('SELECT * FROM content.artifacts WHERE id=%s', (artifact_id,)).fetchone()
                    occurrences[artifact_id] = db.execute('SELECT * FROM content.occurrences WHERE artifact_id=%s ORDER BY ply', (artifact_id,)).fetchall()
            versions = {}
            for row in generation['generation_exercises']:
                versions[row['exercise_key']] = db.execute('SELECT * FROM content.exercise_versions WHERE source_id=%s AND exercise_key=%s AND revision=%s', (reservation['source_id'], row['exercise_key'], row['exercise_revision'])).fetchone()
            return {'artifacts': artifacts, 'occurrences': occurrences, 'versions': versions, 'snapshot_id': snapshot_id, 'database_authority_id': authority, 'schema': schema,
                    'source': source, 'task': task, 'publication': publication, 'operation': operation,
                    'revision': revision, 'snapshot': payload, 'generation': generation}

    @_bounded_contention
    def publish(self, reservation, blobs):
        with self.service._transaction() as db:
            _scope(db, reservation['source_id'])
            source = db.execute('SELECT * FROM content.sources WHERE id=%s FOR UPDATE', (reservation['source_id'],)).fetchone()
            row = db.execute('SELECT * FROM content.final_generation_reservations WHERE reservation_id=%s FOR UPDATE', (reservation['reservation_id'],)).fetchone()
            if not row or row['epoch'] != reservation['epoch']:
                raise ContentError('content_final_handle_invalid', 'Reservation epoch changed')
            if row['state'] == 'captured':
                return 'existing'
            if row['state'] != 'reserved':
                raise ContentError('content_final_source_changed', 'Reservation no longer admits this capture')
            publication = db.execute('SELECT digest,source_id,source_revision FROM content.publications WHERE publication_id=%s AND generation=%s', (row['publication_id'], row['generation'])).fetchone()
            operation = db.execute('SELECT o.state,d.digest FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.id=%s FOR UPDATE OF o', (row['approved_operation_id'],)).fetchone()
            certificate = json.loads(blobs['certificate'])
            if (source['requested_revision'] != blobs['revision'] or source['last_complete_generation'] != row['generation'] or source['last_complete_revision'] != blobs['revision'] or not publication or publication['digest'] != certificate['publication_digest'] or publication['source_id'] != row['source_id'] or publication['source_revision'] != blobs['revision'] or not operation or operation['state'] != 'completed' or operation['digest'] != row['approved_draft_digest']):
                db.execute("UPDATE content.final_generation_reservations SET state='recovery-held',updated_at=now() WHERE reservation_id=%s", (row['reservation_id'],))
                return None
            db.execute('INSERT INTO content.final_generation_captures(capture_id,reservation_id,certificate_bytes,export_bytes,inventory_bytes,proof_bytes,certificate_digest,export_sha256,inventory_sha256,proof_sha256,export_byte_length) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (blobs['capture_id'], row['reservation_id'], blobs['certificate'], blobs['export'], blobs['inventory'], blobs['proof'], blobs['certificate_digest'], blobs['export_sha256'], blobs['inventory_sha256'], blobs['proof_sha256'], len(blobs['export'])))
            db.execute("UPDATE content.final_generation_reservations SET state='captured',capture_id=%s,updated_at=now() WHERE reservation_id=%s", (blobs['capture_id'], row['reservation_id']))
            return blobs


    @_bounded_contention
    def _read_capture(self, reservation):
        with self.service._transaction() as db:
            _scope(db, reservation['source_id'])
            source = db.execute('SELECT * FROM content.sources WHERE id=%s FOR UPDATE', (reservation['source_id'],)).fetchone()
            row = db.execute('SELECT * FROM content.final_generation_reservations WHERE reservation_id=%s FOR UPDATE', (reservation['reservation_id'],)).fetchone()
            if not row or row['epoch'] != reservation['epoch']:
                raise ContentError('content_final_handle_invalid', 'Reservation epoch changed')
            capture = db.execute('SELECT * FROM content.final_generation_captures WHERE reservation_id=%s', (row['reservation_id'],)).fetchone()
            if not capture:
                return row, None, None
            blobs = {key: bytes(capture[key + '_bytes']) for key in ('certificate', 'export', 'inventory', 'proof')}
            hashes_match = (hashlib.sha256(blobs['export']).hexdigest() == capture['export_sha256']
                            and hashlib.sha256(blobs['inventory']).hexdigest() == capture['inventory_sha256']
                            and hashlib.sha256(blobs['proof']).hexdigest() == capture['proof_sha256']
                            and len(blobs['export']) == capture['export_byte_length'])
            try:
                certificate = json.loads(blobs['certificate'])
                proof = json.loads(blobs['proof'])
                inventory = json.loads(blobs['inventory'])
                authority = str(db.execute('SELECT database_authority_id FROM content.final_generation_authority WHERE singleton=1').fetchone()['database_authority_id'])
                hashes_match = (_canonical(certificate) == blobs['certificate'] and _canonical(proof) == blobs['proof'] and _canonical(inventory) == blobs['inventory'] and certificate['database_authority_id'] == authority and certificate['workspace_id'] == row['workspace_id'] and certificate['source_id'] == row['source_id'] and certificate['publication_id'] == row['publication_id'] and certificate['generation'] == row['generation'] and certificate['transfer_id'] == row['transfer_id'] and proof['database_authority_id'] == authority and proof['workspace_id'] == row['workspace_id'] and proof['source_id'] == row['source_id'] and proof['reservation_id'] == row['reservation_id'] and proof['transfer_id'] == row['transfer_id'] and proof['epoch'] == row['epoch']) and hashes_match
                hashes_match = hashes_match and certificate['certificate_digest'] == capture['certificate_digest'] and _hash({k:v for k,v in certificate.items() if k != 'certificate_digest'}) == capture['certificate_digest'] and certificate['receipt_id'] == capture['capture_id'] and certificate['fence_id'] == row['reservation_id']
                current_revision = db.execute('SELECT digest FROM content.source_revisions WHERE source_id=%s AND revision=%s', (row['source_id'], certificate['source_revision'])).fetchone()
                current_publication = db.execute('SELECT digest,source_id,source_revision FROM content.publications WHERE publication_id=%s AND generation=%s', (row['publication_id'], row['generation'])).fetchone()
                from tempo_content.generation_envelope import validate_content_generation_envelope
                envelope = validate_content_generation_envelope(certificate_json=blobs['certificate'], export_bytes=blobs['export'], expected_transfer_id=row['transfer_id'])
                hashes_match = hashes_match and envelope.status == 'ENVELOPE_VALID' and _canonical(certificate['inventory']) == blobs['inventory']
                current = (source['requested_revision'] == certificate['source_revision'] and source['last_complete_revision'] == certificate['source_revision'] and source['last_complete_generation'] == certificate['generation'] and current_revision and current_revision['digest'] == certificate['source_digest'] and current_publication and current_publication['digest'] == certificate['publication_digest'] and current_publication['source_id'] == row['source_id'] and current_publication['source_revision'] == certificate['source_revision'])
            except (ValueError, KeyError, TypeError):
                hashes_match, current = False, False
            if not hashes_match or not current:
                db.execute("UPDATE content.final_generation_reservations SET state='recovery-held',updated_at=now() WHERE reservation_id=%s", (row['reservation_id'],))
                return row, None, 'content_final_immutable_conflict' if not hashes_match else 'content_final_source_changed'
            return row, {**blobs, 'capture_id':capture['capture_id'], 'certificate_digest':capture['certificate_digest'], 'export_sha256':capture['export_sha256'], 'inventory_sha256':capture['inventory_sha256'], 'proof_sha256':capture['proof_sha256']}, None

    def capture(self, reservation, handle):
        from tempo_content.final_generation import FinalGenerationResult
        from tempo_content.domain import canonical_source, build_publication
        from tempo_content.models import SourceSnapshot
        from tempo_content.policy.source import binding_responses
        from tempo_content.bridge.application import BridgeApplication
        from tempo_content.generation_envelope import validate_content_generation_envelope
        from tempo_content_publication_contracts.v1 import serialize_contract
        from types import SimpleNamespace
        row, previous, error = self._read_capture(reservation)
        if error:
            return FinalGenerationResult('BLOCK', error, row['reservation_id'], row['epoch'], handle, state='recovery-held')
        if previous:
            if row['state'] == 'recovery-held':
                return FinalGenerationResult('BLOCK','content_final_recovery_required',row['reservation_id'],row['epoch'],handle,state=row['state'])
            return self._capture_result(row, handle, previous)
        if row['state'] != 'reserved':
            return FinalGenerationResult('BLOCK', 'content_final_immutable_conflict', row['reservation_id'], row['epoch'], handle, state=row['state'])
        try:
            selected = self.selected(row)
        except ContentError as error:
            if error.code in ('content_final_inventory_mismatch','content_final_capture_limit'):
                return self._hold(row,handle,error.code)
            raise
        source, task, publication, operation, revision = (selected[k] for k in ('source', 'task', 'publication', 'operation', 'revision'))
        snapshot = SourceSnapshot.model_validate(selected['snapshot'])
        canonical_source_bytes = canonical_source(snapshot).encode()
        digest_source = hashlib.sha256(canonical_source_bytes).hexdigest()
        if (source['requested_revision'] != task['revision'] or source['last_complete_revision'] != task['revision'] or source['last_complete_generation'] != row['generation'] or task['state'] != 'complete' or publication['source_id'] != row['source_id'] or publication['source_revision'] != task['revision'] or revision['digest'] != digest_source or publication['provenance']['snapshot'] != snapshot.model_dump(mode='json') or operation['state'] != 'completed' or operation['draft_digest'] != row['approved_draft_digest'] or _hash(operation['draft']) != row['approved_draft_digest'] or operation['draft']['source'] != json.loads(canonical_source_bytes)):
            return self._hold(row, handle, 'content_final_source_changed')
        manifest = operation['draft']['manifest']
        if manifest.get('export_profile') != 'learning' or manifest.get('complete') is not True:
            return self._hold(row, handle, 'content_final_profile_not_approved')
        if manifest.get('historical_mapping_unknown'):
            return self._hold(row, handle, 'content_final_inventory_mismatch')
        generation = selected['generation']
        memberships = {(x.route_key, x.policy_key, x.repertoire_key) for x in snapshot.memberships}
        stored_memberships = {(x['route_key'], x['policy_key'], x['repertoire_key']) for x in generation['generation_routes']}
        exercises = {x.key: x for x in snapshot.exercises}
        generation_exercises = {x['exercise_key']: x['exercise_revision'] for x in generation['generation_exercises']}
        bindings = {(x.route_key, x.policy_key, x.ply, x.exercise_key) for x in snapshot.bindings}
        responses = {(x['route_key'], x['policy_key'], x['ply'], x['exercise_key']) for x in generation['responses']}
        if len(memberships) != len(snapshot.memberships) or memberships != stored_memberships or set(exercises) != set(generation_exercises) or len(bindings) != len(snapshot.bindings) or bindings != responses:
            return self._hold(row, handle, 'content_final_inventory_mismatch')
        routes = {x.key:x for x in snapshot.routes}
        repertoires = {x.key:x for x in snapshot.repertoires}
        for item in generation['generation_routes']:
            route = routes[item['route_key']]
            color = repertoires[item['repertoire_key']].learner_color
            artifact = selected['artifacts'][item['artifact_id']]
            expected_artifact = _hash(['content-legal-v1/chess-1.11.2', route.starting_fen, route.moves, color])
            if not artifact or item['artifact_id'] != expected_artifact or not artifact['complete'] or artifact['starting_fen'] != route.starting_fen or artifact['moves'] != route.moves or artifact['learner_color'] != color:
                return self._hold(row, handle, 'content_final_inventory_mismatch')
        for key, exercise in exercises.items():
            data = exercise.model_dump(mode='json')
            version = selected['versions'][key]
            if not version or version['digest'] != _hash({k:data[k] for k in ('starting_fen','prompt','specification')}):
                return self._hold(row, handle, 'content_final_inventory_mismatch')
        route_rows = {(x['route_key'],x['policy_key']): x for x in generation['generation_routes']}
        binding_rows = {(x.route_key,x.policy_key,x.ply,x.exercise_key): x for x in snapshot.bindings}
        for response in generation['responses']:
            identity = (response['route_key'],response['policy_key'],response['ply'],response['exercise_key'])
            route_row = route_rows[(response['route_key'],response['policy_key'])]
            occurrence = next((o for o in selected['occurrences'][route_row['artifact_id']] if o['ply'] == response['ply']), None)
            if not occurrence:
                return self._hold(row, handle, 'content_final_inventory_mismatch')
            occurrence_fact = {**occurrence, 'position_key': occurrence['position_key']}
            try:
                accepted = binding_responses(exercises[response['exercise_key']].model_dump(mode='json'), binding_rows[identity].model_dump(mode='json'), {'complete':True}, occurrence_fact)
            except (ValueError, KeyError):
                return self._hold(row, handle, 'content_final_inventory_mismatch')
            if response['position_key'] != occurrence['position_key'] or response['accepted_moves'] != accepted:
                return self._hold(row, handle, 'content_final_inventory_mismatch')
        expected_publication = build_publication(snapshot, task['revision'], row['generation'], task['built_at'], generation_exercises)
        if serialize_contract(expected_publication) != publication['payload'] or hashlib.sha256(publication['payload'].encode()).hexdigest() != publication['digest']:
            return self._hold(row, handle, 'content_final_inventory_mismatch')
        storage = SimpleNamespace(publication=lambda *_: publication)
        exported = BridgeApplication(SimpleNamespace(workspace_id=self.service.workspace_id), storage).export(row['publication_id'], row['generation'], manifest)
        export_bytes = _canonical(exported)
        triples = ('repertoire_id','route_id','policy_id')
        route_list = exported['routes']
        inventory = {'definitions':exported['publication']['definitions'], 'routes':route_list,
                     'memberships':[{k:r[k] for k in triples} for r in route_list],
                     'bindings':[{**{k:r[k] for k in triples},'binding':b} for r in route_list for b in r['bindings']],
                     'aliases':[{'card_id':k,'legacy_id':v} for k,v in exported['legacy_aliases'].items()]}
        src_identity = {'workspace_id':exported['workspace_id'], 'source_id':exported['source']['source_id'], 'source_key':exported['source']['source_key']}
        inventory['provenance'] = [{'kind':'export_provenance','identity':src_identity,'digest':_hash(exported['provenance'])}, {'kind':'export_publication','identity':{'workspace_id':exported['workspace_id'],'publication_id':row['publication_id'],'generation':row['generation']},'digest':_hash(exported['publication'])}, {'kind':'export_source','identity':src_identity,'digest':_hash(exported['source'])}]
        capture_id = str(uuid.uuid4())
        schema = selected['schema']
        certificate = {'contract_version':'C-FENCE.v1','workspace_id':row['workspace_id'],'database_authority_id':selected['database_authority_id'],'source_id':row['source_id'],'source_key':source['source_key'],'publication_id':row['publication_id'],'source_digest':revision['digest'],'publication_digest':publication['digest'],'export_digest':exported['export_digest'],'export_sha256':hashlib.sha256(export_bytes).hexdigest(),'approved_manifest_digest':_hash(manifest),'snapshot_id':selected['snapshot_id'],'snapshot_schema_identity':_hash(schema),'fence_id':row['reservation_id'],'fence_owner':'Content','transfer_id':row['transfer_id'],'receipt_id':capture_id,'export_profile':'learning','source_revision':task['revision'],'generation':row['generation'],'requested_revision':source['requested_revision'],'complete_revision':source['last_complete_revision'],'complete_generation':source['last_complete_generation'],'export_byte_length':len(export_bytes),'definition_count':len(inventory['definitions']),'route_count':len(inventory['routes']),'membership_count':len(inventory['memberships']),'binding_count':len(inventory['bindings']),'alias_count':len(inventory['aliases']),'inventory':inventory}
        certificate['certificate_digest'] = _hash(certificate)
        certificate_bytes = _canonical(certificate)
        proof = {'contract_version':'C-FENCE.PRODUCER.v1','database_authority_id':selected['database_authority_id'],'workspace_id':row['workspace_id'],'source_id':row['source_id'],'reservation_id':row['reservation_id'],'transfer_id':row['transfer_id'],'epoch':row['epoch'],'snapshot':{'id':selected['snapshot_id'],'schema':schema},'source_revision_record':{k:revision[k] for k in ('revision','digest','stage_id')},'source_snapshot':snapshot.model_dump(mode='json'),'approved_operation':{'id':operation['id'],'state':operation['state'],'draft_id':operation['draft_id'],'draft_digest':operation['draft_digest'],'document':operation['draft']},'task':{k:task[k] for k in ('generation','revision','state','built_at')},'publication':{'payload_sha256':hashlib.sha256(publication['payload'].encode()).hexdigest(), **{k:publication[k] for k in ('source_id','source_revision','publication_id','generation','digest','provenance')}},'source_inventory':{k:snapshot.model_dump(mode='json')[k] for k in ('repertoires','policies','routes','memberships','exercises','bindings')},'generation_inventory':{'routes':generation['generation_routes'],'exercises':generation['generation_exercises'],'responses':generation['responses']},'authored_assets':[],'authored_assets_policy':'stored_source_snapshot_only_no_external_asset_claim'}
        proof_bytes, inventory_bytes = _canonical(proof), _canonical(inventory)
        if any(len(blob)>64*1024*1024 for blob in (certificate_bytes,export_bytes,proof_bytes,inventory_bytes)):
            return self._hold(row, handle, 'content_final_capture_limit')
        validated = validate_content_generation_envelope(certificate_json=certificate_bytes, export_bytes=export_bytes, expected_transfer_id=row['transfer_id'])
        if validated.status != 'ENVELOPE_VALID':
            return self._hold(row, handle, 'content_final_inventory_mismatch')
        blobs = {'capture_id':capture_id, 'certificate':certificate_bytes,'export':export_bytes,'inventory':inventory_bytes,'proof':proof_bytes,'certificate_digest':certificate['certificate_digest'],'export_sha256':certificate['export_sha256'],'inventory_sha256':hashlib.sha256(inventory_bytes).hexdigest(),'proof_sha256':hashlib.sha256(proof_bytes).hexdigest(),'revision':task['revision']}
        published = self.publish(row, blobs)
        if published is None:
            return FinalGenerationResult('BLOCK','content_final_source_changed',row['reservation_id'],row['epoch'],handle,state='recovery-held')
        if published == 'existing':
            current, original, error = self._read_capture(row)
            if error or not original:
                return FinalGenerationResult('BLOCK',error or 'content_final_immutable_conflict',row['reservation_id'],row['epoch'],handle,state='recovery-held')
            return self._capture_result(current, handle, original)
        return self._capture_result(row, handle, blobs)

    @_bounded_contention
    def _hold(self, reservation, handle, code):
        from tempo_content.final_generation import FinalGenerationResult
        with self.service._transaction() as db:
            _scope(db, reservation['source_id'])
            db.execute('SELECT id FROM content.sources WHERE id=%s FOR UPDATE', (reservation['source_id'],)).fetchone()
            row = db.execute('SELECT * FROM content.final_generation_reservations WHERE reservation_id=%s FOR UPDATE', (reservation['reservation_id'],)).fetchone()
            if row and row['epoch'] == reservation['epoch'] and row['state'] in ('reserved','captured','recovery-held'):
                db.execute("UPDATE content.final_generation_reservations SET state='recovery-held',updated_at=now() WHERE reservation_id=%s", (row['reservation_id'],))
        return FinalGenerationResult('BLOCK',code,reservation['reservation_id'],reservation['epoch'],handle,state='recovery-held')

    def _capture_result(self, row, handle, blobs):
        from tempo_content.final_generation import ContentFinalCaptureEvidence, FinalGenerationResult
        cert = json.loads(blobs['certificate'])
        evidence = ContentFinalCaptureEvidence('C-FENCE.PRODUCER.v1',cert['database_authority_id'],row['workspace_id'],row['source_id'],row['transfer_id'],row['reservation_id'],row['epoch'],blobs['capture_id'],blobs['certificate_digest'],blobs['export_sha256'],blobs['inventory_sha256'],blobs['proof_sha256'],'captured',blobs['certificate'],blobs['export'],blobs['inventory'],blobs['proof'])
        return FinalGenerationResult('CONTENT_CAPTURE_VERIFIED','content_final_captured',row['reservation_id'],row['epoch'],handle,evidence,'captured')

    @_bounded_contention
    def resolve(self, reservation, handle, intent, reader):
        from dataclasses import asdict
        from psycopg.types.json import Jsonb
        from tempo_content.final_generation import AcceptedTarget, ProvenNoTargetWrite, FinalGenerationResult
        row, capture, error = self._read_capture(reservation)
        if error:
            return FinalGenerationResult('BLOCK',error,row['reservation_id'],row['epoch'],handle,state='recovery-held')
        if reader is None or (not capture and intent == 'accept'):
            return self._hold(row,handle,'content_final_target_unknown')
        try:
            disposition = reader.read_content_transfer(row['transfer_id'], capture['certificate_digest'] if capture else None)
        except Exception:
            return self._hold(row,handle,'content_final_target_unknown')
        if intent == 'accept' and type(disposition) is AcceptedTarget:
            strings = (disposition.authority_id,disposition.allocation_id,disposition.legacy_watermark,disposition.activation_receipt,disposition.checkpoint,disposition.receipt_digest)
            valid = all(type(x) is str and x for x in strings) and type(disposition.allocation_generation) is int and disposition.allocation_generation > 0 and disposition.transfer_id == row['transfer_id'] and disposition.certificate_digest == capture['certificate_digest'] and disposition.export_sha256 == capture['export_sha256']
            terminal = 'accepted'
        elif intent == 'abort' and type(disposition) is ProvenNoTargetWrite:
            valid = all(type(x) is str and x for x in (disposition.authority_id,disposition.allocation_id,disposition.closed_disposition)) and disposition.transfer_id == row['transfer_id']
            terminal = 'aborted'
        else:
            valid = False
        if not valid:
            return self._hold(row,handle,'content_final_target_unknown')
        with self.service._transaction() as db:
            _scope(db,row['source_id'])
            db.execute('SELECT id FROM content.sources WHERE id=%s FOR UPDATE',(row['source_id'],)).fetchone()
            current = db.execute('SELECT * FROM content.final_generation_reservations WHERE reservation_id=%s FOR UPDATE',(row['reservation_id'],)).fetchone()
            if not current or current['epoch'] != row['epoch'] or current['capture_id'] != row['capture_id'] or current['state'] not in ('reserved','captured','recovery-held'):
                return FinalGenerationResult('BLOCK','content_final_source_changed',row['reservation_id'],row['epoch'],handle,state=current['state'] if current else None)
            db.execute('UPDATE content.final_generation_reservations SET state=%s,resolution_json=%s,updated_at=now() WHERE reservation_id=%s',(terminal,Jsonb(asdict(disposition)),row['reservation_id']))
        return FinalGenerationResult(terminal.upper(),'content_final_'+terminal,row['reservation_id'],row['epoch'],state=terminal)


def make_final_generation(service, target_disposition_reader=None):
    """Explicit composition; the caller owns the service and reader lifetimes."""
    from tempo_content.final_generation import ContentFinalGeneration
    return ContentFinalGeneration(FinalGenerationStore(service), target_disposition_reader)
