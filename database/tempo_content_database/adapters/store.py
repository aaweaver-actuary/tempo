"""Content-only PostgreSQL authority; computation happens between short sections."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import time
import uuid

from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from tempo_content_publication_contracts.v1 import Publication

from tempo_content.models import SourceSnapshot
from tempo_content.policy import prepare_source, publication_report, coverage_reply, inventory_report
from tempo_content.worker import reconcile_once, reconcile_binding, reconcile_exercise, reconcile_route
from tempo_content.publication import build_source_publication, admit_publication
from tempo_content.domain import ContentError, stable_id, canonical_source, canonical_position
from tempo_content_database.ddl.migrate import readiness

INVENTORY_VERSION = 'content-legal-v1/chess-1.11.2'
KINDS = ('repertoires','policies','routes','memberships','exercises','bindings')


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _hook(hook,event,**context):
    if hook:
        hook(event,context)


def _decode_inventory_cursor(after,kind,workspace_id,source_key,source_revision,generation):
    if after is None:
        return '' if kind=='positions' else ['','']
    try:
        if type(after) is not str:
            raise ValueError('cursor must be a JSON string')
        cursor = json.loads(after)
        required = {'version','workspace_id','kind','source_key','source_revision','generation','last'}
        if not isinstance(cursor,dict) or not required<=cursor.keys():
            raise ValueError('missing cursor fields')
        if type(cursor['version']) is not int or cursor['version']!=1:
            raise ValueError('unsupported cursor version')
        for field in ('source_revision','generation'):
            if type(cursor[field]) is not int or cursor[field]<1:
                raise ValueError('invalid cursor revision or generation')
        for field,expected in (('workspace_id',workspace_id),('source_key',source_key),('kind',kind)):
            if type(cursor[field]) is not str or cursor[field]!=expected:
                raise ValueError('cursor belongs to another workspace, source, or reader')
        last = cursor['last']
        if kind=='positions':
            if type(last) is not str or not last:
                raise ValueError('position cursor requires a position key')
        elif type(last) is not list or len(last)!=2 or any(type(key) is not str or not key for key in last):
            raise ValueError('route cursor requires a route/policy pair')
    except (ValueError,TypeError,RecursionError) as error:
        raise ContentError('invalid_cursor','Invalid versioned inventory cursor: '+str(error)) from error
    if (cursor['source_revision'],cursor['generation'])!=(source_revision,generation):
        raise ContentError('stale_inventory_cursor','Source inventory changed; restart pagination without --after')
    return last


class ContentService:
    def __init__(self,database_url,workspace_id,pool_size=2):
        self.workspace_id = workspace_id
        self.pool = ConnectionPool(database_url,min_size=0,max_size=pool_size,open=False,kwargs={'row_factory':dict_row})
        self.pool.open()
        self.transactions = []
        try:
            with self.pool.connection() as database:
                database.row_factory = tuple_row
                try:
                    readiness(database,workspace_id)
                finally:
                    database.row_factory = dict_row
        except Exception:
            self.pool.close()
            raise

    def close(self):
        self.pool.close()

    def __enter__(self):
        return self

    def __exit__(self,*arguments):
        self.close()

    @contextmanager
    def _transaction(self):
        started = time.perf_counter()
        try:
            with self.pool.connection() as database:
                database.execute("SET LOCAL lock_timeout='5s'")
                database.execute("SET LOCAL statement_timeout='10s'")
                yield database
        finally:
            self.transactions.append(time.perf_counter()-started)
            if len(self.transactions)>10000:
                del self.transactions[:5000]

    @contextmanager
    def _read_snapshot(self):
        with self.pool.connection() as database:
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            database.execute("SET LOCAL lock_timeout='5s'")
            database.execute("SET LOCAL statement_timeout='10s'")
            yield database

    def _query(self,query,parameters=()):
        with self.pool.connection() as database:
            return database.execute(query,parameters).fetchall()

    def _one(self,query,parameters=()):
        rows = self._query(query,parameters)
        return rows[0] if rows else None

    def _source(self,source_key):
        row = self._one('SELECT * FROM content.sources WHERE source_key=%s',(source_key,))
        if not row:
            raise ContentError('source_not_found',source_key)
        return row

    def _lock_source(self,database,source_id,hook=None):
        row = database.execute('SELECT * FROM content.sources WHERE id=%s FOR UPDATE',(source_id,)).fetchone()
        _hook(hook,'after_source_lock',source_id=source_id)
        return row

    def _manifest(self,source_id,revision):
        row = self._one('SELECT m.* FROM content.input_manifests m JOIN content.source_revisions r ON r.stage_id=m.stage_id WHERE r.source_id=%s AND r.revision=%s',(source_id,revision))
        if not row:
            raise ContentError('revision_not_found',str(revision))
        return row

    def _item(self,stage_id,kind,index=None,key=None):
        predicate,value = ('ordinal',index) if key is None else ('item_key',key)
        row = self._one('SELECT payload FROM content.input_items WHERE stage_id=%s AND kind=%s AND '+predicate+'=%s',(stage_id,kind,value))
        if not row:
            raise ContentError('source_item_missing',kind+':'+str(value))
        return row['payload']

    def _snapshot(self,manifest):
        payload = dict(manifest['header'])
        for kind in KINDS:
            values = []
            offset = 0
            while offset < manifest['counts'][kind]:
                page = self._query('SELECT payload FROM content.input_items WHERE stage_id=%s AND kind=%s AND ordinal>=%s ORDER BY ordinal LIMIT 64',(manifest['stage_id'],kind,offset))
                if not page:
                    raise ContentError('source_page_missing',kind)
                values.extend(row['payload'] for row in page)
                offset += len(page)
            payload[kind] = values
        snapshot = SourceSnapshot.model_validate(payload)
        if any(len(payload[kind])!=manifest['counts'][kind] for kind in KINDS) or _digest(canonical_source(snapshot))!=manifest['digest']:
            raise ContentError('source_manifest_mismatch','Immutable source count/digest differs from its manifest')
        return snapshot

    def _exercise_revisions(self,source_id,generation):
        revisions = {}
        after = ''
        while True:
            page = self._query('SELECT exercise_key,exercise_revision FROM content.generation_exercises WHERE source_id=%s AND generation=%s AND exercise_key>%s ORDER BY exercise_key LIMIT 64',(source_id,generation,after))
            revisions.update((row['exercise_key'],row['exercise_revision']) for row in page)
            if len(page)<64:
                return revisions
            after = page[-1]['exercise_key']

    def load_source(self,snapshot,expected_revision=0,hook=None,operation_id=None):
        snapshot, payload, digest, source_id, publication_id, stage_id = prepare_source(
            snapshot, expected_revision, self.workspace_id, digest_text=_digest)
        with self._read_snapshot() as database:
            current = database.execute('SELECT s.requested_revision,r.digest,t.generation,t.state FROM content.sources s LEFT JOIN content.source_revisions r ON r.source_id=s.id AND r.revision=s.requested_revision LEFT JOIN LATERAL (SELECT generation,state FROM content.tasks WHERE source_id=s.id AND revision=s.requested_revision ORDER BY generation DESC LIMIT 1) t ON true WHERE s.id=%s',(source_id,)).fetchone()
            if current and (current['digest'] is None or current['generation'] is None):
                raise ContentError('inventory_incomplete','Current source revision or task is missing')
            if current and current['digest']==digest:
                return {'source_id':source_id,'source_revision':current['requested_revision'],'generation':current['generation'],'status':current['state'],'replayed':True}
            if (current['requested_revision'] if current else 0)!=expected_revision:
                raise ContentError('stale_source_revision','Expected source revision differs')
        counts = {kind:len(payload[kind]) for kind in KINDS}
        # Each staged section is bounded. A crash before manifest/admission leaves
        # only immutable resumable input, never a requested source revision.
        for kind in KINDS:
            for offset in range(0,counts[kind],64):
                with self._transaction() as database:
                    for ordinal,item in enumerate(payload[kind][offset:offset+64],offset):
                        key = item.get('key',str(ordinal))
                        existing = database.execute('SELECT payload FROM content.input_items WHERE stage_id=%s AND kind=%s AND item_key=%s',(stage_id,kind,key)).fetchone()
                        if existing and existing['payload'] != item:
                            raise ContentError('staging_conflict','Immutable input page differs')
                        database.execute('INSERT INTO content.input_items VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(stage_id,kind,key,ordinal,Jsonb(item)))
        header = {key:value for key,value in payload.items() if key not in KINDS}
        with self._transaction() as database:
            actual = {row['kind']:row['count'] for row in database.execute('SELECT kind,count(*) FROM content.input_items WHERE stage_id=%s GROUP BY kind',(stage_id,)).fetchall()}
            if any(actual.get(kind,0)!=count for kind,count in counts.items()):
                raise ContentError('incomplete_staging','Staged input count differs')
            database.execute('INSERT INTO content.input_manifests VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(stage_id,source_id,digest,Jsonb(header),Jsonb(counts)))
            persisted = database.execute('SELECT source_id,digest,header,counts FROM content.input_manifests WHERE stage_id=%s',(stage_id,)).fetchone()
            if (persisted['source_id'],persisted['digest'],persisted['header'],persisted['counts'])!=(source_id,digest,header,counts):
                raise ContentError('source_manifest_conflict','Immutable source manifest differs from accepted content')
        _hook(hook,'after_staging',source_id=source_id,stage_id=stage_id)
        with self._transaction() as database:
            from .bridge import lock_scope
            lock_scope(database,source_id,operation_id)
            if operation_id is not None:
                authority = database.execute('SELECT state,source_id FROM content.bridge_operations WHERE id=%s FOR UPDATE', (operation_id,)).fetchone()
                if authority is None or authority['source_id'] != source_id or authority['state'] != 'applying':
                    raise ContentError('application_not_authorized', 'Source admission requires the original still-applying operation')
            database.execute('INSERT INTO content.sources(id,source_key,publication_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(source_id,snapshot.source_key,publication_id))
            source = self._lock_source(database,source_id,hook)
            previous = database.execute('SELECT digest FROM content.source_revisions WHERE source_id=%s AND revision=%s',(source_id,source['requested_revision'])).fetchone()
            task = database.execute('SELECT * FROM content.tasks WHERE source_id=%s ORDER BY generation DESC LIMIT 1 FOR UPDATE',(source_id,)).fetchone()
            if previous and previous['digest']==digest:
                return {'source_id':source_id,'source_revision':source['requested_revision'],'generation':task['generation'],'status':task['state'],'replayed':True}
            if source['requested_revision'] != expected_revision:
                raise ContentError('stale_source_revision','Expected source revision differs')
            revision = source['requested_revision']+1
            generation = task['generation']+1 if task else 1
            # Queue collector before touching any artifact. The collector never
            # acquires a source lock, preventing the donor's task/artifact cycle.
            database.execute('UPDATE content.collector SET pending=true WHERE id=1')
            database.execute('INSERT INTO content.source_revisions VALUES(%s,%s,%s,%s)',(source_id,revision,stage_id,digest))
            database.execute('UPDATE content.sources SET requested_revision=%s WHERE id=%s',(revision,source_id))
            database.execute('INSERT INTO content.tasks(source_id,generation,revision) VALUES(%s,%s,%s)',(source_id,generation,revision))
        return {'source_id':source_id,'source_revision':revision,'generation':generation,'status':'pending','replayed':False}

    def source_show(self,source_key):
        source = self._source(source_key)
        manifest = self._manifest(source['id'],source['requested_revision'])
        return {**source,'snapshot':self._snapshot(manifest).model_dump(mode='json')}

    def repertoire_show(self,repertoire_key,source_key=None):
        with self._read_snapshot() as database:
            if source_key is not None and not database.execute('SELECT 1 FROM content.sources WHERE source_key=%s',(source_key,)).fetchone():
                raise ContentError('source_not_found',source_key)
            query = "SELECT s.source_key,i.payload FROM content.sources s JOIN content.source_revisions r ON r.source_id=s.id AND r.revision=s.requested_revision JOIN content.input_manifests m ON m.stage_id=r.stage_id JOIN content.input_items i ON i.stage_id=m.stage_id WHERE i.kind='repertoires' AND i.item_key=%s"
            parameters = (repertoire_key,)
            if source_key is not None:
                query += ' AND s.source_key=%s'
                parameters += (source_key,)
            matches = database.execute(query+' ORDER BY s.source_key LIMIT 2',parameters).fetchall()
            if not matches:
                raise ContentError('repertoire_not_found',repertoire_key)
            if len(matches)>1:
                raise ContentError('ambiguous_repertoire','Repertoire key matches multiple sources; supply --source-key')
            row = matches[0]
            return {**row['payload'],'repertoire_id':stable_id(self.workspace_id,row['source_key'],'repertoire',repertoire_key),'source_key':row['source_key']}

    def request_inventory(self,source_key):
        # Admission always leaves a durable task; repeated requests preserve it.
        return self.inventory_status(source_key)

    def inventory_status(self,source_key):
        with self._read_snapshot() as database:
            return self._inventory_status(database,source_key)

    def _inventory_status(self,database,source_key):
        source = database.execute('SELECT * FROM content.sources WHERE source_key=%s',(source_key,)).fetchone()
        if not source:
            raise ContentError('source_not_found',source_key)
        task = database.execute('SELECT * FROM content.tasks WHERE source_id=%s AND revision=%s ORDER BY generation DESC LIMIT 1',(source['id'],source['requested_revision'])).fetchone()
        manifest = database.execute('SELECT m.* FROM content.input_manifests m JOIN content.source_revisions r ON r.stage_id=m.stage_id WHERE r.source_id=%s AND r.revision=%s',(source['id'],source['requested_revision'])).fetchone()
        if not task or not manifest:
            raise ContentError('inventory_incomplete','Current source task or manifest is missing')
        if task['state']=='complete':
            for table,kind in (('generation_routes','memberships'),('generation_exercises','exercises'),('responses','bindings')):
                count = database.execute('SELECT count(*) AS count FROM content.'+table+' WHERE source_id=%s AND generation=%s',(source['id'],task['generation'])).fetchone()['count']
                if count!=manifest['counts'][kind]:
                    raise ContentError('inventory_incomplete','Complete generation has missing '+kind)
        return inventory_report(source,task,manifest['counts']['exercises'])

    def _claim(self,source_key,hook):
        query = 'SELECT t.*,s.source_key FROM content.tasks t JOIN content.sources s ON s.id=t.source_id AND s.requested_revision=t.revision WHERE t.state IN (\'pending\',\'working\') AND (t.lease_until IS NULL OR t.lease_until<=clock_timestamp())'
        parameters = ()
        if source_key:
            query += ' AND s.source_key=%s'
            parameters = (source_key,)
        query += ' ORDER BY t.source_id,t.generation DESC LIMIT 1'
        selected = self._one(query,parameters)
        if not selected:
            return None
        with self._transaction() as database:
            source = self._lock_source(database,selected['source_id'],hook)
            task = database.execute('SELECT *,lease_until>clock_timestamp() AS leased FROM content.tasks WHERE source_id=%s AND generation=%s FOR UPDATE',(selected['source_id'],selected['generation'])).fetchone()
            if source['requested_revision']!=task['revision'] or task['state'] not in ('pending','working') or task['leased']:
                return None
            token = str(uuid.uuid4())
            database.execute("UPDATE content.tasks SET state='working',lease_token=%s,lease_until=clock_timestamp()+interval '30 seconds' WHERE source_id=%s AND generation=%s",(token,task['source_id'],task['generation']))
            return {**task,'lease_token':token,'source_key':source['source_key']}

    def _fence(self,database,task,hook=None):
        source = self._lock_source(database,task['source_id'],hook)
        current = database.execute('SELECT *,lease_until>clock_timestamp() AS leased FROM content.tasks WHERE source_id=%s AND generation=%s FOR UPDATE',(task['source_id'],task['generation'])).fetchone()
        if source['requested_revision']!=task['revision'] or current['lease_token']!=task['lease_token'] or current['state']!='working' or not current['leased']:
            raise ContentError('stale_generation','Reconciliation delivery lost authority')
        return current

    def _advance(self,database,task,phase=None,cursor=None,exercise_state=None,state='pending'):
        database.execute('UPDATE content.tasks SET phase=%s,cursor=%s,exercise_state=%s,state=%s,lease_token=NULL,lease_until=NULL WHERE source_id=%s AND generation=%s',(phase or task['phase'],task['cursor'] if cursor is None else cursor,Jsonb(exercise_state),state,task['source_id'],task['generation']))

    def work_once(self,source_key=None,hook=None):
        return reconcile_once(source_key,hook,claim_task=self._claim,collect=self.collect_once,
                              read_manifest=self._manifest,route_slice=self._route_slice,
                              exercise_slice=self._exercise_slice,binding_slice=self._binding_slice,
                              fail_task=self._fail_task)

    def _fail_task(self,task,code,error):
        with self._transaction() as database:
            try:
                self._fence(database,task)
            except ContentError:
                return False
            database.execute("UPDATE content.tasks SET state='failed',error=%s,lease_token=NULL,lease_until=NULL WHERE source_id=%s AND generation=%s",(Jsonb({'code':code,'message':str(error)}),task['source_id'],task['generation']))
        return True

    def _transition(self,task,phase,state='pending',hook=None):
        _hook(hook,'after_read',task=task)
        _hook(hook,'before_commit',task=task)
        with self._transaction() as database:
            self._fence(database,task,hook)
            self._advance(database,task,phase=phase,cursor=0,state=state)
        return {'worked':True,'status':state,'phase':phase}

    def _route_slice(self,task,manifest,hook):
        return reconcile_route(task,manifest,hook,transition=self._transition,read_item=self._item,
                               read_artifact=self._route_artifact,register_route=self._register_route,
                               commit_segment=self._commit_route_segment,notify=_hook)

    def _route_artifact(self,route,color):
        artifact_id = _digest(_json([INVENTORY_VERSION,route['starting_fen'],route['moves'],color]))
        artifact = self._one('SELECT * FROM content.artifacts WHERE id=%s',(artifact_id,))
        return artifact_id, artifact

    def _register_route(self,task,membership,route,color,artifact_id,hook):
        with self._transaction() as database:
            self._fence(database,task,hook)
            database.execute('INSERT INTO content.artifacts(id,starting_fen,moves,learner_color,version,next_fen) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(artifact_id,route['starting_fen'],Jsonb(route['moves']),color,INVENTORY_VERSION,route['starting_fen']))
            database.execute('SELECT id FROM content.artifacts WHERE id=%s FOR UPDATE',(artifact_id,)).fetchone()
            _hook(hook,'after_artifact_lock',artifact_id=artifact_id,action='register')
            self._attach_route(database,task,membership,artifact_id)
            self._advance(database,task)
            _hook(hook,'after_writes',task=task)

    def _commit_route_segment(self,task,membership,artifact_id,artifact,segment,hook):
        first, moves, terminal, occurrences, end_fen = segment
        with self._transaction() as database:
            self._fence(database,task,hook)
            current = database.execute('SELECT * FROM content.artifacts WHERE id=%s FOR UPDATE',(artifact_id,)).fetchone()
            if not current or (current['epoch'],current['cursor'])!=(artifact['epoch'],artifact['cursor']):
                self._advance(database,task)
                return False
            _hook(hook,'after_artifact_lock',artifact_id=artifact_id,action='traverse')
            self._attach_route(database,task,membership,artifact_id)
            self._save_occurrences(database,artifact_id,occurrences)
            complete = artifact['complete'] or terminal
            database.execute('UPDATE content.artifacts SET cursor=%s,next_fen=%s,complete=%s WHERE id=%s',(first+len(moves),end_fen,complete,artifact_id))
            self._advance(database,task,cursor=task['cursor']+int(complete))
            _hook(hook,'after_writes',task=task)
        return True

    def _attach_route(self,database,task,membership,artifact_id):
        database.execute('INSERT INTO content.generation_routes VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(task['source_id'],task['generation'],membership['route_key'],membership['policy_key'],membership['repertoire_key'],artifact_id))

    def _save_occurrences(self,database,artifact_id,occurrences):
        positions = {}
        for occurrence in occurrences:
            positions[occurrence['fen_key']] = occurrence['fen']
            for reply in occurrence['legal_replies']:
                positions[reply['resulting_fen_key']] = reply['resulting_fen_key']+' 0 1'
        with database.cursor() as cursor:
            cursor.executemany('INSERT INTO content.positions(position_key,fen,side_to_move) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',[(key,fen,key.split()[1]) for key,fen in sorted(positions.items())])
        # Include result nodes: reply foreign keys take KEY SHARE locks there.
        # Sorted NO KEY UPDATE locks prevent opposite-color shared-universe
        # cycles while remaining compatible with those foreign-key locks.
        if positions:
            database.execute('SELECT position_key FROM content.positions WHERE position_key=ANY(%s) ORDER BY position_key FOR NO KEY UPDATE',(sorted(positions),)).fetchall()
        for occurrence in sorted(occurrences,key=lambda item:(item['fen_key'],item['ply'])):
            key = occurrence['fen_key']
            if occurrence['opponent']:
                replies = occurrence['legal_replies']
                fingerprint = _digest(_json([[item['move_uci'],item['resulting_fen_key']] for item in replies]))
                current = database.execute('SELECT legal_version,fingerprint FROM content.positions WHERE position_key=%s',(key,)).fetchone()
                if current['legal_version'] and (current['legal_version'],current['fingerprint'])!=(INVENTORY_VERSION,fingerprint):
                    raise ContentError('legal_universe_conflict','Canonical legal universe changed without versioning')
                with database.cursor() as cursor:
                    cursor.executemany('INSERT INTO content.legal_replies VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',[(key,reply['move_uci'],reply['resulting_fen_key']) for reply in replies])
                database.execute('UPDATE content.positions SET legal_version=%s,reply_count=%s,fingerprint=%s WHERE position_key=%s',(INVENTORY_VERSION,len(replies),fingerprint,key))
            database.execute('INSERT INTO content.occurrences VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(artifact_id,occurrence['ply'],occurrence['fen'],key,occurrence['opponent'],occurrence['authored_move']))

    def _exercise_slice(self,task,manifest,hook):
        return reconcile_exercise(task,manifest,hook,transition=self._transition,read_item=self._item,
                                  commit_exercise=self._commit_exercise,notify=_hook)

    def _commit_exercise(self,task,payload,exercise,continuation,hook):
        with self._transaction() as database:
            self._fence(database,task,hook)
            if continuation is not None:
                self._advance(database,task,exercise_state=continuation)
                _hook(hook,'after_writes',task=task)
                return False
            identity = database.execute('SELECT * FROM content.exercise_identities WHERE source_id=%s AND exercise_key=%s',(task['source_id'],exercise.key)).fetchone()
            semantics = (exercise.policy_key,exercise.decision_key,exercise.specification.type)
            if identity and (identity['policy_key'],identity['decision_key'],identity['kind'])!=semantics:
                raise ContentError('exercise_identity_changed','Use a new stable exercise key for changed policy/decision/kind')
            predecessor = getattr(exercise,'supersedes_key',None)
            if predecessor and not identity:
                if not database.execute('SELECT 1 FROM content.exercise_identities WHERE source_id=%s AND exercise_key=%s',(task['source_id'],predecessor)).fetchone():
                    raise ContentError('predecessor_not_found',predecessor)
            if identity and identity['supersedes_key']!=predecessor:
                raise ContentError('predecessor_mapping_conflict','Exercise predecessor is immutable')
            database.execute('INSERT INTO content.exercise_identities VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(task['source_id'],exercise.key,stable_id(self.workspace_id,task['source_key'],'card',exercise.key),*semantics,predecessor))
            digest = _digest(_json({key:payload[key] for key in ('starting_fen','prompt','specification')}))
            previous = database.execute('SELECT revision,digest FROM content.exercise_versions WHERE source_id=%s AND exercise_key=%s ORDER BY revision DESC LIMIT 1',(task['source_id'],exercise.key)).fetchone()
            revision = previous['revision'] if previous and previous['digest']==digest else (previous['revision']+1 if previous else 1)
            database.execute('INSERT INTO content.exercise_versions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(task['source_id'],exercise.key,revision,digest))
            database.execute('INSERT INTO content.generation_exercises VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(task['source_id'],task['generation'],exercise.key,revision))
            self._advance(database,task,cursor=task['cursor']+1)
            _hook(hook,'after_writes',task=task)
        return True

    def _binding_slice(self,task,manifest,hook):
        return reconcile_binding(task,manifest,hook,transition=self._transition,read_item=self._item,
                                 read_target=self._binding_target,commit_binding=self._commit_binding,notify=_hook)

    def _binding_target(self,task,binding):
        route = self._one('SELECT r.*,a.complete FROM content.generation_routes r JOIN content.artifacts a ON a.id=r.artifact_id WHERE r.source_id=%s AND r.generation=%s AND r.route_key=%s AND r.policy_key=%s',(task['source_id'],task['generation'],binding['route_key'],binding['policy_key']))
        occurrence = self._one('SELECT * FROM content.occurrences WHERE artifact_id=%s AND ply=%s',(route['artifact_id'],binding['ply'])) if route else None
        return route, occurrence

    def _commit_binding(self,task,binding,occurrence,accepted,hook):
        with self._transaction() as database:
            self._fence(database,task,hook)
            database.execute('INSERT INTO content.responses VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(task['source_id'],task['generation'],binding['route_key'],binding['policy_key'],binding['ply'],binding['exercise_key'],occurrence['position_key'],Jsonb(accepted)))
            self._advance(database,task,cursor=task['cursor']+1)
            _hook(hook,'after_writes',task=task)

    def collect_once(self,hook=None):
        with self._transaction() as database:
            collector = database.execute('SELECT * FROM content.collector WHERE id=1 FOR UPDATE SKIP LOCKED').fetchone()
            if not collector or not collector['pending']:
                return {'worked':False,'status':'idle','phase':'collect'}
            # Remove only a bounded set of superseded memberships first. Old
            # publication attachments do not reference this disposable cache.
            obsolete = database.execute('SELECT r.source_id,r.generation,r.route_key,r.policy_key FROM content.generation_routes r JOIN content.tasks t USING(source_id,generation) JOIN content.sources s ON s.id=r.source_id WHERE t.revision<>s.requested_revision ORDER BY r.source_id,r.generation,r.route_key,r.policy_key LIMIT 64').fetchall()
            if obsolete:
                for row in obsolete:
                    database.execute('DELETE FROM content.generation_routes WHERE source_id=%s AND generation=%s AND route_key=%s AND policy_key=%s',tuple(row.values()))
                database.execute('UPDATE content.collector SET deliveries=deliveries+1 WHERE id=1')
                return {'worked':True,'status':'pending','phase':'collect','memberships_removed':len(obsolete),'occurrences_removed':0}
            artifact = database.execute('SELECT a.* FROM content.artifacts a WHERE NOT EXISTS(SELECT 1 FROM content.generation_routes r WHERE r.artifact_id=a.id) ORDER BY a.id LIMIT 1 FOR UPDATE OF a SKIP LOCKED').fetchone()
            if artifact:
                _hook(hook,'after_artifact_lock',artifact_id=artifact['id'],action='collect')
                # Membership insertion obtains this artifact lock too. Recheck
                # after locking to close selection-versus-registration races.
                if database.execute('SELECT 1 FROM content.generation_routes WHERE artifact_id=%s LIMIT 1',(artifact['id'],)).fetchone():
                    return {'worked':False,'status':'pending','phase':'collect'}
                database.execute('UPDATE content.artifacts SET complete=false,cursor=0,next_fen=starting_fen,epoch=epoch+1 WHERE id=%s',(artifact['id'],))
                removed = database.execute('DELETE FROM content.occurrences WHERE artifact_id=%s AND ply IN (SELECT ply FROM content.occurrences WHERE artifact_id=%s ORDER BY ply LIMIT 64) RETURNING ply',(artifact['id'],artifact['id'])).fetchall()
                if not database.execute('SELECT 1 FROM content.occurrences WHERE artifact_id=%s LIMIT 1',(artifact['id'],)).fetchone():
                    database.execute('DELETE FROM content.artifacts WHERE id=%s',(artifact['id'],))
                database.execute('UPDATE content.collector SET deliveries=deliveries+1 WHERE id=1')
                return {'worked':True,'status':'pending','phase':'collect','occurrences_removed':len(removed)}
            # Locked orphans remain pending; no false completion or lost wakeup.
            remains = database.execute('SELECT 1 FROM content.artifacts a WHERE NOT EXISTS(SELECT 1 FROM content.generation_routes r WHERE r.artifact_id=a.id) LIMIT 1').fetchone()
            if not remains:
                database.execute('UPDATE content.collector SET pending=false WHERE id=1')
            return {'worked':False,'status':'pending' if remains else 'idle','phase':'collect'}

    def publication_build(self,source_key,hook=None):
        return build_source_publication(source_key,hook,inventory_version=INVENTORY_VERSION,
            read_source=self._source,read_task=self._publication_task,read_payload=self._publication_payload,
            read_manifest=self._manifest,reserve_build=self._reserve_publication_build,read_snapshot=self._snapshot,
            read_revisions=self._exercise_revisions,persist=self.store_publication,notify=_hook)

    def _publication_task(self,source_id,revision):
        return self._one('SELECT * FROM content.tasks WHERE source_id=%s AND revision=%s ORDER BY generation DESC LIMIT 1',(source_id,revision))

    def _publication_payload(self,publication_id,generation):
        return self._one('SELECT payload FROM content.publications WHERE publication_id=%s AND generation=%s',(publication_id,generation))

    def _reserve_publication_build(self,source,task):
        with self._transaction() as database:
            current = self._lock_source(database,source['id'])
            if current['requested_revision']!=task['revision']:
                raise ContentError('stale_generation','Source changed before build')
            locked = database.execute('SELECT * FROM content.tasks WHERE source_id=%s AND generation=%s FOR UPDATE',(source['id'],task['generation'])).fetchone()
            built_at = locked['built_at'] or datetime.now(timezone.utc).isoformat()
            database.execute('UPDATE content.tasks SET built_at=%s WHERE source_id=%s AND generation=%s',(built_at,source['id'],task['generation']))
        return built_at

    def store_publication(self,publication,source_id,provenance,hook=None):
        return admit_publication(publication,source_id,provenance,hook,inventory_version=INVENTORY_VERSION,
            digest_text=_digest,read_payload=self._publication_payload,read_manifest=self._manifest,
            read_revisions=self._exercise_revisions,read_snapshot=self._snapshot,commit_publication=self._commit_publication)

    def _commit_publication(self,publication,source_id,provenance,payload,digest,expected_payload,expected_provenance,hook):
        """Immutable insert boundary; complete source authority remains mandatory."""
        with self._transaction() as database:
            source = self._lock_source(database,source_id,hook)
            existing = database.execute('SELECT payload,digest,source_id,provenance FROM content.publications WHERE publication_id=%s AND generation=%s',(publication.publication_id,publication.publication_generation)).fetchone()
            if existing:
                if existing['payload']!=payload or existing['digest']!=digest or existing['source_id']!=source_id or existing['provenance']!=provenance:
                    raise ContentError('publication_conflict','Publication identity is immutable')
                return
            if expected_payload!=payload:
                raise ContentError('publication_definition_mismatch','Publication differs from the validated authored source')
            task = database.execute('SELECT * FROM content.tasks WHERE source_id=%s AND generation=%s FOR UPDATE',(source_id,publication.publication_generation)).fetchone()
            if not task or task['state']!='complete' or task['built_at']!=publication.built_at or source['requested_revision']!=publication.source_revision or task['revision']!=publication.source_revision or source['publication_id']!=publication.publication_id or publication.workspace_id!=self.workspace_id or source['last_complete_generation']>publication.publication_generation:
                raise ContentError('stale_generation','Publication does not bind current complete inputs')
            if provenance!=expected_provenance:
                raise ContentError('publication_provenance_mismatch','Publication provenance differs from the authored source')
            incomplete = database.execute('SELECT 1 FROM content.generation_routes r JOIN content.artifacts a ON a.id=r.artifact_id WHERE r.source_id=%s AND r.generation=%s AND NOT a.complete LIMIT 1',(source_id,task['generation'])).fetchone()
            if incomplete:
                raise ContentError('inventory_incomplete','Route cache is incomplete')
            database.execute('INSERT INTO content.publications VALUES(%s,%s,%s,%s,%s,%s,%s)',(publication.publication_id,publication.publication_generation,source_id,publication.source_revision,payload,digest,Jsonb(provenance)))
            database.execute('UPDATE content.sources SET last_complete_revision=%s,last_complete_generation=%s WHERE id=%s',(publication.source_revision,publication.publication_generation,source_id))

    def publication_show(self,publication_id,generation=None):
        query = 'SELECT payload FROM content.publications WHERE publication_id=%s'
        parameters = (publication_id,)
        if generation is not None:
            query += ' AND generation=%s'
            parameters += (generation,)
        row = self._one(query+' ORDER BY generation DESC LIMIT 1',parameters)
        if not row:
            raise ContentError('publication_not_found',publication_id)
        return Publication.model_validate_json(row['payload'])

    def publication_status(self,source_key):
        status = self.inventory_status(source_key)
        manifest = self._manifest(status['source_id'],status['revision'])
        return publication_report(self.workspace_id,status,manifest['counts']['exercises'])

    def _page(self,limit):
        if type(limit) is not int or not 1<=limit<=256:
            raise ContentError('invalid_page_size','Page size must be 1..256')

    def _page_metadata(self,status):
        return {'source_revision':status['revision'],'generation':status['generation'],'status':status['status'],'stale':status['stale']}

    def _page_cursor(self,status,kind,last):
        return _json({'version':1,'workspace_id':self.workspace_id,'kind':kind,'source_key':status['source_key'],
                      'source_revision':status['revision'],'generation':status['generation'],'last':last})

    def positions(self,source_key,limit=64,after=None):
        self._page(limit)
        with self._read_snapshot() as database:
            status = self._inventory_status(database,source_key)
            cursor = _decode_inventory_cursor(after,'positions',self.workspace_id,source_key,status['revision'],status['generation'])
            rows = database.execute('SELECT DISTINCT p.* FROM content.positions p JOIN content.occurrences o ON o.position_key=p.position_key JOIN content.generation_routes r ON r.artifact_id=o.artifact_id WHERE r.source_id=%s AND r.generation=%s AND o.opponent AND p.position_key>%s ORDER BY p.position_key LIMIT %s',(status['source_id'],status['generation'],cursor,limit)).fetchall()
            return {**self._page_metadata(status),'items':rows,'next_cursor':self._page_cursor(status,'positions',rows[-1]['position_key']) if len(rows)==limit else None}

    def replies(self,position_key,limit=64,after=None):
        self._page(limit)
        rows = self._query('SELECT * FROM content.legal_replies WHERE position_key=%s AND move_uci>%s ORDER BY move_uci LIMIT %s',(position_key,after or '',limit))
        return {'items':rows,'next_cursor':rows[-1]['move_uci'] if len(rows)==limit else None}

    def routes(self,source_key,limit=64,after=None):
        self._page(limit)
        with self._read_snapshot() as database:
            status = self._inventory_status(database,source_key)
            cursor = _decode_inventory_cursor(after,'routes',self.workspace_id,source_key,status['revision'],status['generation'])
            rows = database.execute('SELECT r.*,a.complete,a.cursor,a.epoch FROM content.generation_routes r JOIN content.artifacts a ON a.id=r.artifact_id WHERE r.source_id=%s AND r.generation=%s AND (r.route_key,r.policy_key)>(%s,%s) ORDER BY r.route_key,r.policy_key LIMIT %s',(status['source_id'],status['generation'],*cursor,limit)).fetchall()
            return {**self._page_metadata(status),'items':rows,'next_cursor':self._page_cursor(status,'routes',[rows[-1]['route_key'],rows[-1]['policy_key']]) if len(rows)==limit else None}

    def coverage(self,source_key,route_key,policy_key,ply,hook=None):
        with self._read_snapshot() as database:
            status = self._inventory_status(database,source_key)
            metadata = {'source_revision':status['revision'],'generation':status['generation']}
            if status['state']!='complete':
                return {**metadata,'status':'unavailable','items':[],'stale':True}
            route = database.execute('SELECT r.*,a.complete FROM content.generation_routes r JOIN content.artifacts a ON a.id=r.artifact_id WHERE r.source_id=%s AND r.generation=%s AND route_key=%s AND policy_key=%s',(status['source_id'],status['generation'],route_key,policy_key)).fetchone()
            if not route:
                raise ContentError('route_not_found',route_key)
            if not route['complete']:
                raise ContentError('inventory_incomplete','Complete generation has an incomplete route artifact')
            occurrence = database.execute('SELECT * FROM content.occurrences WHERE artifact_id=%s AND ply=%s',(route['artifact_id'],ply)).fetchone()
            if not occurrence:
                raise ContentError('inventory_incomplete','Requested route occurrence is missing')
            if not occurrence['opponent']:
                raise ContentError('opponent_position_required',str(ply))
            _hook(hook,'after_coverage_occurrence',generation=status['generation'],occurrence=occurrence)
            position = database.execute('SELECT * FROM content.positions WHERE position_key=%s',(occurrence['position_key'],)).fetchone()
            replies = database.execute('SELECT * FROM content.legal_replies WHERE position_key=%s ORDER BY move_uci',(occurrence['position_key'],)).fetchall()
            fingerprint = _digest(_json([[row['move_uci'],row['resulting_position_key']] for row in replies]))
            if not position or (position['legal_version'],position['reply_count'],position['fingerprint'])!=(INVENTORY_VERSION,len(replies),fingerprint):
                raise ContentError('inventory_incomplete','Canonical legal-reply inventory is missing or inconsistent')
            missing_response_occurrence = database.execute('SELECT 1 FROM content.responses p LEFT JOIN content.generation_routes r USING(source_id,generation,route_key,policy_key) LEFT JOIN content.occurrences o ON o.artifact_id=r.artifact_id AND o.ply=p.ply WHERE p.source_id=%s AND p.generation=%s AND (o.position_key IS DISTINCT FROM p.position_key OR o.opponent) LIMIT 1',(status['source_id'],status['generation'])).fetchone()
            if missing_response_occurrence:
                raise ContentError('inventory_incomplete','Validated response occurrence is missing or inconsistent')
            result = []
            for reply in replies:
                authored = reply['move_uci']==occurrence['authored_move']
                direct = database.execute('SELECT 1 FROM content.responses p JOIN content.occurrences o ON o.artifact_id=%s AND o.ply=p.ply WHERE p.source_id=%s AND p.generation=%s AND p.route_key=%s AND p.policy_key=%s AND p.ply=%s AND p.position_key=%s AND jsonb_array_length(p.accepted_moves)>0 AND (o.authored_move IS NULL OR p.accepted_moves ? o.authored_move)',(route['artifact_id'],status['source_id'],status['generation'],route_key,policy_key,ply+1,reply['resulting_position_key'])).fetchone() if authored else None
                transposed = database.execute('SELECT 1 FROM content.responses p JOIN content.generation_routes r USING(source_id,generation,route_key,policy_key) WHERE p.source_id=%s AND p.generation=%s AND p.policy_key=%s AND r.repertoire_key=%s AND p.position_key=%s AND (p.route_key,p.ply)<>(%s,%s) AND jsonb_array_length(p.accepted_moves)>0 LIMIT 1',(status['source_id'],status['generation'],policy_key,route['repertoire_key'],reply['resulting_position_key'],route_key,ply+1)).fetchone()
                result.append(coverage_reply(reply,authored,direct,transposed))
            return {**metadata,'status':'complete','position_key':occurrence['position_key'],'items':result,'stale':False}
