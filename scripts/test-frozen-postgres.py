"""Disposable PostgreSQL proof for frozen order: no live target accepted."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'scripts'))
import psycopg
from app.postgres_store import PostgresConnection,tempo_row_factory,close_pools
from app.schema_version import POSTGRES_SCHEMA_VERSION
from app.services import frozen_opening_order as order,postgres_queue_refresh as refresh
from app.services.frozen_maia_plan import create_plan
from app.services.cards import card_id
from provision_postgres_roles import provision
import chess


def command(arguments):
    return subprocess.run(arguments,text=True,capture_output=True,check=True).stdout.strip()


def fake_maia(fen):
    legal=[move.uci() for move in chess.Board(fen).legal_moves]
    return [{'move_uci':move,'probability':.6 if move=='e7e5' else .4/(len(legal)-1)} for move in legal]


def model_provenance():
    files={'scripts/maia-inference.mjs','app/lib/all_moves_maia3.json','app/lib/all_moves_maia3_reversed.json',
        *(f'public/maia3/parts/part-{index:02d}' for index in range(6))}
    return {'rating_self':1500,'rating_opponent':1500,'model':{'checksums':{path:'0'*64 for path in files},'runtime':'fixture','node':'v26'},
        'implementation_checksums':{'backend/app/services/frozen_maia_math.py':'1'*64,
            'backend/app/services/frozen_maia_plan.py':'1'*64,'scripts/frozen-maia-order.py':'1'*64}}


def main():
    # This disposable PostgreSQL proof owns local admission, not the separate
    # shared-host Redis/HTTP gate qualification or any ambient live endpoint.
    os.environ.pop('TEMPO_REDIS_URL',None)
    os.environ.pop('TEMPO_FOREGROUND_ACTIVITY_URL',None)
    suffix=secrets.token_hex(5);container='tempo-frozen-'+suffix;volume=container+'-data'
    password=secrets.token_urlsafe(24)
    reader_password=secrets.token_urlsafe(24);writer_password=secrets.token_urlsafe(24)
    credential_directory=Path(tempfile.mkdtemp(prefix='tempo-frozen-role-proof-'))
    started=time.monotonic()
    (ROOT/'test-results').mkdir(parents=True,exist_ok=True)
    try:
        command(['docker','volume','create','--label','tempo.owner='+container,'--label','tempo.disposable=true',volume])
        command(['docker','run','-d','--name',container,'--label','tempo.owner='+container,'--label','tempo.disposable=true',
                 '--cpus','1','--memory','384m','--pids-limit','256','-e','POSTGRES_PASSWORD='+password,
                 '-p','127.0.0.1::5432','-v',volume+':/var/lib/postgresql','postgres:18.6-trixie'])
        caps=json.loads(command(['docker','container','inspect',container,'--format',
                                '{"cpu_nanos":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}}}']))
        assert caps=={'cpu_nanos':1_000_000_000,'memory_bytes':384*1024*1024}
        print(json.dumps({'stage':'frozen-native','pid':os.getpid(),'pgid':os.getpgid(0),
                          'container':container,'volume':volume,'observed_caps':caps}),flush=True)
        port=command(['docker','inspect',container,'--format','{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}'])
        url=f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
        for attempt in range(100):
            try:
                connection=psycopg.connect(url,connect_timeout=1);connection.close();break
            except psycopg.OperationalError:time.sleep(.1)
        else:raise RuntimeError('Disposable database never ready')
        spec=importlib.util.spec_from_file_location('migrations',ROOT/'scripts/apply_postgres_migrations.py')
        migrations=importlib.util.module_from_spec(spec);spec.loader.exec_module(migrations)
        migrations.apply_migrations(url)
        provision(url,reader_password,writer_password)
        port=command(['docker','inspect',container,'--format','{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}'])
        (credential_directory/'pgpass').write_text(
            f'127.0.0.1:{port}:postgres:tempo_reader:{reader_password}\n'
            f'127.0.0.1:{port}:postgres:tempo_writer:{writer_password}\n',
        )
        (credential_directory/'pgpass').chmod(0o600)
        reader_url=f'postgresql://tempo_reader@127.0.0.1:{port}/postgres'
        writer_url=f'postgresql://tempo_writer@127.0.0.1:{port}/postgres'
        os.environ['PGPASSFILE']=str(credential_directory/'pgpass')
        os.environ['TEMPO_DATABASE_READ_URL']=reader_url;os.environ['TEMPO_DATABASE_WRITE_URL']=writer_url
        day='2026-10-14';ids=[]
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('fixture','Fixture','test',?)",(day,))
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('fixture',1,'ready','2026-10-14')")
            database.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            database.execute('UPDATE settings SET study_only_mode=1,new_cards_per_day=2 WHERE id=1')
            # Seed 100 confidently scored cards and one localized unknown route.
            # One deferred card stays below the production 1% safety threshold.
            board=chess.Board();board.push_uci('e2e4')
            scored_lines=[]
            for black_move in sorted(board.legal_moves,key=lambda move:move.uci()):
                if black_move.uci()=='a7a6':continue
                after_black=board.copy();after_black.push(black_move)
                for white_move in sorted(after_black.legal_moves,key=lambda move:move.uci()):
                    scored_lines.append(['e2e4',black_move.uci(),white_move.uci()])
                    if len(scored_lines)==100:break
                if len(scored_lines)==100:break
            for index,moves in enumerate(scored_lines):
                identifier=card_id(chess.STARTING_FEN,moves)
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,'fixture','prefix',?,?,'new',?,'opening','white')",(identifier,chess.STARTING_FEN,json.dumps(moves),day))
                line_id=f'line-{index:03d}'
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'fixture','fixture','white',?,?,?)",(line_id,chess.STARTING_FEN,json.dumps(moves),day+str(index)))
                database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES('fixture',1,?,0,?,NULL,?, ?,?,'white','prefix',0,0,'[]')",(line_id,identifier,' '.join(chess.STARTING_FEN.split()[:4]),chess.STARTING_FEN,json.dumps(moves)))
            missing_board=chess.Board();missing_board.push_uci('e2e4');missing_board.push_uci('a7a6')
            missing_fen=missing_board.fen();unknown_moves=['g1f3'];unknown_id=card_id(missing_fen,unknown_moves)
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,'fixture','prefix',?,?,'new',?,'opening','white')",(unknown_id,missing_fen,json.dumps(unknown_moves),day))
            database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('missing-line','fixture','missing','white',?,?,?)",(missing_fen,json.dumps(unknown_moves),day+'missing'))
            database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES('fixture',1,'missing-line',0,?,NULL,?, ?,?,'white','prefix',0,0,'[]')",(unknown_id,' '.join(missing_fen.split()[:4]),missing_fen,json.dumps(unknown_moves)))

        cli_spec=importlib.util.spec_from_file_location('frozen_cli',ROOT/'scripts/frozen-maia-order.py')
        cli=importlib.util.module_from_spec(cli_spec);cli_spec.loader.exec_module(cli)
        config={'scenarios':[{'name':'Fixture','repertoire_id':'fixture','color':'white','prefix':['e4'],'weight':1.0}],
            'expected_database':'postgres','expected_schema':'public','maia_elo_override':None,'study_timezone':'America/New_York'}
        snapshot=cli.capture()
        plan=create_plan(snapshot,config,fake_maia,model_provenance(),allow_unscored_tail=True)
        assert plan['activation_safe'] and plan['deferred_count']==1 and plan['scored_count']==100
        assert plan['deferred_fraction']<=0.01 and plan['lineage_diagnostic_count']==1
        assert plan['deferred_cards'][0]['id']==unknown_id and plan['deferred_cards'][0]['marginal_score'] is None
        strict=create_plan(snapshot,config,fake_maia,model_provenance())
        try:cli.validate_plan(strict)
        except ValueError as error:assert 'incomplete' in str(error).lower()
        else:raise AssertionError('Strict incomplete plan unexpectedly validated')
        cli.validate_plan(plan)
        ids=[plan['ranks'][1]['id'],plan['ranks'][0]['id']]
        run_id=plan['run_id']
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            order.stage_run(database,plan,day);order.stage_ranks(database,plan,plan['ranks']);order.activate_run(database,plan)
            saved=json.loads(database.execute('SELECT plan_json FROM frozen_opening_runs WHERE run_id=?',(run_id,)).fetchone()[0])
            assert saved['scoring_policy']=='allow_unscored_tail' and saved['deferred_count']==1
        page=refresh._prepare_study_opening_page(day,{})
        assert [row['card_id'] for row in page['items']]==list(reversed(ids)),page
        original_validation=order.proposal_valid
        validation_count=0
        def content_race(database,planned):
            nonlocal validation_count
            valid=original_validation(database,planned);validation_count+=1
            if validation_count==1:
                with psycopg.connect(url) as concurrent:
                    concurrent.execute('UPDATE cards SET revision=2 WHERE id=%s',(planned['card_id'],))
            return valid
        order.proposal_valid=content_race
        try:
            with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
                assert not refresh._admit_one_prioritized_opening(PostgresConnection(raw),day,page['items'][0])
            assert validation_count==2
        finally:order.proposal_valid=original_validation
        with psycopg.connect(url) as raw:raw.execute('UPDATE cards SET revision=1 WHERE id=%s',(ids[1],))
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            for item in page['items']:assert refresh._admit_one_prioritized_opening(database,day,item)
            assert not refresh._admit_one_prioritized_opening(database,day,page['items'][0])
            assert database.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0]==2
            assert not order.activate_run(database,plan)
            database.execute('UPDATE daily_queue SET position=1-position WHERE queue_date=?',(day,))
        next_page=refresh._prepare_study_opening_page('2026-10-15',{})
        next_ids=[row['card_id'] for row in next_page['items']]
        expected_next=[row['id'] for row in plan['ranks'][2:2+len(next_ids)]]
        assert next_ids==expected_next and unknown_id not in next_ids
        prepared=refresh._prepare_queue_randomization(day)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            assert refresh._publish_queue_randomization(PostgresConnection(raw),day,prepared)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            restored=order.restore_queue_changes(database,run_id)
            assert len(restored['restored_queue_entries'])==2 and not restored['queue_conflicts']
        # A saved v3 presentation is replayed, including this explicit restoration.
        # Deliberately invalidate only this fixture's policy receipt to exercise a
        # fresh rank application below; ordinary fetch/refresh must not reshuffle.
        assert refresh._prepare_queue_randomization(day)['entries'] is None
        with psycopg.connect(url) as raw:
            raw.execute("UPDATE daily_queue_days SET membership_hash='fixture-unmixed' WHERE queue_date=%s",(day,))
        prepared=refresh._prepare_queue_randomization(day)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            assert refresh._publish_queue_randomization(PostgresConnection(raw),day,prepared)
        # A new issued head between prepare and publish must invalidate the plan.
        prepared_before_issue=refresh._prepare_queue_randomization(day)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id=?',(ids[1],))
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            assert not refresh._publish_queue_randomization(PostgresConnection(raw),day,prepared_before_issue)
        # Changed unstarted content is held by the actual serving predicate.
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute('UPDATE cards SET revision=2 WHERE id=?',(ids[0],))
            from app.services.queue_application import available_attempt_sql
            _AVAILABLE_ATTEMPT_SQL=available_attempt_sql()
            assert [row[0] for row in database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall()]==[database.execute('SELECT id FROM daily_queue WHERE card_id=?',(ids[1],)).fetchone()[0]]
            database.execute('UPDATE cards SET revision=1 WHERE id=?',(ids[0],))
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('changed-membership','changed','test',?)",(day,))
            database.execute("INSERT INTO repertoire_cards(card_id,repertoire_id) VALUES(?,'changed-membership')",(ids[0],))
            assert len(database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall())==1
            database.execute("DELETE FROM repertoire_cards WHERE repertoire_id='changed-membership'")
        # Exercise the actual tail, with earlier locked ranks retaining their gates.
        tail_day='2026-10-15'
        final_scored_id=plan['ranks'][-2]['id']
        tail_ids=[final_scored_id,unknown_id]
        with psycopg.connect(url) as raw:
            raw.execute("UPDATE cards SET state='locked' WHERE introduced_at IS NULL AND id<>%s",(final_scored_id,))
        locked_tail=refresh._prepare_study_opening_page(tail_day,{})
        assert [item['card_id'] for item in locked_tail['items']]==[final_scored_id]
        with psycopg.connect(url) as raw:
            raw.execute("UPDATE cards SET state='new',pending_validation=1 WHERE id=%s",(unknown_id,))
        assert [item['card_id'] for item in refresh._prepare_study_opening_page(tail_day,{})['items']]==[final_scored_id]
        with psycopg.connect(url) as raw:
            raw.execute('UPDATE cards SET pending_validation=0,revision=2 WHERE id=%s',(unknown_id,))
        assert [item['card_id'] for item in refresh._prepare_study_opening_page(tail_day,{})['items']]==[final_scored_id]
        with psycopg.connect(url) as raw:
            raw.execute('UPDATE cards SET revision=1 WHERE id=%s',(unknown_id,))
        tail_page=refresh._prepare_study_opening_page(tail_day,{})
        assert [item['card_id'] for item in tail_page['items']]==tail_ids
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            for item in tail_page['items']:assert refresh._admit_one_prioritized_opening(database,tail_day,item)
            assert not refresh._admit_one_prioritized_opening(database,tail_day,tail_page['items'][-1])
            database.execute('UPDATE daily_queue SET position=1-position WHERE queue_date=?',(tail_day,))
        tail_refresh=refresh._prepare_queue_randomization(tail_day)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            assert refresh._publish_queue_randomization(database,tail_day,tail_refresh)
            assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position',(tail_day,))]==tail_ids
        # Exercise the capture relation across multiple pages, including a
        # composite key and all three freshness-filtered priority views.
        cli_spec=importlib.util.spec_from_file_location('frozen_cli',ROOT/'scripts/frozen-maia-order.py')
        cli=importlib.util.module_from_spec(cli_spec);cli_spec.loader.exec_module(cli)
        pagination_ids=[]
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            scope_generation=database.execute('SELECT generation FROM repertoire_game_scope WHERE id=1').fetchone()[0]
            prefix_revision=database.execute('SELECT canonical_prefix_revision FROM repertoires WHERE id=?',('fixture',)).fetchone()[0]
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('reader-page-game','fixture','fixture',?,'rapid',1,'white','win',?,'[]')",(day,chess.STARTING_FEN))
            database.execute("INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('fixture',7,?)",(day,))
            for index in range(260):
                identifier=f'reader-page-{index:04d}';pagination_ids.append(identifier)
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,'fixture','prefix',?,'[]','new',?,'opening','white')",(identifier,chess.STARTING_FEN,day))
                evidence=json.dumps({'canonical_prefix_revision':prefix_revision,
                                     'game_scope_generation':scope_generation if index<259 else scope_generation-1})
                database.execute("INSERT INTO repertoire_card_introduction_priorities(repertoire_id,card_id,scoring_version,evidence_json,updated_at) VALUES('fixture',?,1,?,?)",(identifier,evidence,day))
                database.execute("INSERT INTO repertoire_card_priority_generations(repertoire_id,generation,card_id,scoring_version,evidence_json,updated_at) VALUES('fixture',7,?,1,?,?)",(identifier,evidence,day))
                finding=f'reader-page-finding-{index:04d}'
                database.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,card_id,status,created_at,updated_at) VALUES(?,'reader-page-game',1,1,'fixture',1,'{}',?,'accepted',?,?)",(finding,identifier,day,day))
                database.execute("INSERT INTO gameplay_card_priorities(card_id,source_game_id,finding_id,priority_date,reason,created_at) VALUES(?,'reader-page-game',?,?,?,?)",(identifier,finding,day,'fixture',day))
                database.execute("INSERT INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,attempt_failed,last_status,legacy,start_fen,moves_json,content_type) VALUES(?,?,?,?,0,'new',0,'queued',1,?,'[]','opening')",(100000+index,identifier,1,day,chess.STARTING_FEN))

        @contextmanager
        def reader_factory():
            with psycopg.connect(reader_url,row_factory=tempo_row_factory) as raw:
                yield PostgresConnection(raw)

        with psycopg.connect(reader_url,row_factory=tempo_row_factory) as reader:
            identity=reader.execute("SELECT current_user,current_setting('transaction_read_only'),role.rolsuper,role.rolconfig FROM pg_roles role WHERE role.rolname=current_user").fetchone()
            assert identity['current_user']=='tempo_reader' and identity['current_setting']=='on' and not identity['rolsuper']
            assert 'default_transaction_read_only=on' in identity['rolconfig']
            for relation in set(cli.TABLES.values()):
                grants=reader.execute("SELECT c.relowner::regrole::text AS owner,has_table_privilege(current_user,%s,'SELECT') AS can_select,has_table_privilege(current_user,%s,'INSERT') AS can_insert,has_table_privilege(current_user,%s,'UPDATE') AS can_update,has_table_privilege(current_user,%s,'DELETE') AS can_delete FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s",(relation,relation,relation,relation,relation)).fetchone()
                assert grants and grants['owner']!='tempo_reader' and grants['can_select'] and not any(grants[key] for key in ('can_insert','can_update','can_delete')),(relation,grants)

        captured=cli.capture(reader_factory)
        assert len(captured['cards'])==361 and captured['metadata'][0]['schema_version']==POSTGRES_SCHEMA_VERSION
        assert [row['id'] for row in captured['cards'] if row['id'] in pagination_ids]==pagination_ids
        assert len({row['id'] for row in captured['cards']})==len(captured['cards'])
        assert len([row for row in captured['origins'] if row['queue_entry_id']>=100000])==260
        assert len([row for row in captured['priorities'] if row['card_id'] in pagination_ids])==259
        assert len([row for row in captured['generated'] if row['card_id'] in pagination_ids])==259
        assert len([row for row in captured['gameplay'] if row['card_id'] in pagination_ids])==260
        close_pools()
        command(['docker','restart',container])
        port=command(['docker','inspect',container,'--format','{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}'])
        url=f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
        reader_url=f'postgresql://tempo_reader@127.0.0.1:{port}/postgres'
        writer_url=f'postgresql://tempo_writer@127.0.0.1:{port}/postgres'
        (credential_directory/'pgpass').write_text(
            f'127.0.0.1:{port}:postgres:tempo_reader:{reader_password}\n'
            f'127.0.0.1:{port}:postgres:tempo_writer:{writer_password}\n',
        )
        (credential_directory/'pgpass').chmod(0o600)
        os.environ['TEMPO_DATABASE_READ_URL']=reader_url;os.environ['TEMPO_DATABASE_WRITE_URL']=writer_url
        for attempt in range(100):
            try:
                with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
                    database=PostgresConnection(raw)
                    assert order.activation(database)[0]==run_id
                    assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position',(day,))]==list(reversed(ids))
                    assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position',(tail_day,))]==tail_ids
                break
            except psycopg.OperationalError:time.sleep(.1)
        else:raise RuntimeError('Restart proof timed out')
        assert not refresh._prepare_study_opening_page('2026-10-16',{})['items']
        # Add two real published post-snapshot routes plus one uncertain legacy
        # route after activation. Leave spare allowance and no selectable frozen
        # card so ordinary fallback and legacy protection are independently visible.
        def add_post_snapshot_card(moves,label):
            identifier=card_id(chess.STARTING_FEN,moves);line_id=f'post-snapshot-{label}'
            with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
                database=PostgresConnection(raw)
                moves_json=json.dumps(moves)
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,'fixture','prefix',?,?,'new',?,'opening','white')",(identifier,chess.STARTING_FEN,moves_json,day))
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'fixture',?,'white',?,?,?)",(line_id,label,chess.STARTING_FEN,moves_json,day+label))
                database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES('fixture',1,?,0,?,NULL,?,?,?,'white','prefix',0,0,'[]')",(line_id,identifier,' '.join(chess.STARTING_FEN.split()[:4]),chess.STARTING_FEN,moves_json))
            return identifier

        fallback_id=add_post_snapshot_card(['e2e4','e7e5','g1f3','b8c6','f1b5'],'fallback')
        race_id=add_post_snapshot_card(['e2e4','c7c5','g1f3','d7d6','d2d4','c5d4','f3d4'],'race')
        legacy_id=add_post_snapshot_card(['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4'],'legacy')
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,999,'new','fixture')",(day,legacy_id))
            legacy_entry=database.execute('SELECT id FROM daily_queue WHERE queue_date=? AND card_id=?',(day,legacy_id)).fetchone()[0]
            database.execute('UPDATE queue_attempt_origins SET legacy=1 WHERE queue_entry_id=? AND card_id=?',(legacy_entry,legacy_id))
            database.execute('DELETE FROM daily_queue WHERE id=?',(legacy_entry,))
            database.execute("UPDATE cards SET state='locked' WHERE id IN (SELECT card_id FROM frozen_opening_ranks WHERE run_id=? AND card_id<>?) AND introduced_at IS NULL",(run_id,unknown_id))
            database.execute('UPDATE settings SET new_cards_per_day=4 WHERE id=1')
        fallback_page=refresh._prepare_study_opening_page(tail_day,{})
        assert {item['card_id'] for item in fallback_page['items']}=={fallback_id,race_id},fallback_page
        old_race_proposal=next(item for item in fallback_page['items'] if item['card_id']==race_id)
        with psycopg.connect(url) as concurrent:
            concurrent.execute("INSERT INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,attempt_failed,last_status,legacy,start_fen,moves_json,content_type) VALUES(800001,%s,1,%s,0,'new',0,'queued',1,%s,'[]','opening')",(race_id,tail_day,chess.STARTING_FEN))
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            assert refresh._admit_one_prioritized_opening(database,tail_day,next(item for item in fallback_page['items'] if item['card_id']==fallback_id))
            assert not refresh._admit_one_prioritized_opening(database,tail_day,old_race_proposal)
            assert database.execute('SELECT introduced_at FROM cards WHERE id=?',(race_id,)).fetchone()[0] is None
            assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(race_id,)).fetchone()[0]==0
            assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(fallback_id,)).fetchone()[0]==1
            legacy_proposal={'card_id':legacy_id,'reason':None,'frozen_token':list(order.activation(database))}
            assert not order.proposal_valid(database,legacy_proposal)
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,1000,'new','fixture')",(day,legacy_id))
            legacy_queue_id=database.execute('SELECT id FROM daily_queue WHERE queue_date=? AND card_id=?',(day,legacy_id)).fetchone()[0]
            database.execute('UPDATE queue_attempt_origins SET legacy=1 WHERE queue_entry_id=? AND card_id=?',(legacy_queue_id,legacy_id))
            from app.services.queue_application import available_attempt_sql
            _AVAILABLE_ATTEMPT_SQL=available_attempt_sql()
            assert legacy_queue_id not in [row[0] for row in database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall()]
            database.execute("UPDATE daily_queue SET admission_kind='explicit' WHERE id=?",(legacy_queue_id,))
            assert legacy_queue_id in [row[0] for row in database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall()]
            database.execute("UPDATE daily_queue SET admission_kind='new',attempt_state='attempted' WHERE id=?",(legacy_queue_id,))
            database.execute('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=? AND card_id=?',(legacy_queue_id,legacy_id))
            assert legacy_queue_id in [row[0] for row in database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall()]
        from app import main as application
        # The native fixture deliberately spans fixed queue days. Match the API's
        # clock to its owning queue instead of reading the host calendar.
        class FixtureDay(date):
            @classmethod
            def today(cls):
                return cls.fromisoformat(day)
        with patch.object(application, 'date', FixtureDay):
            assert legacy_id in {row['id'] for row in application._queue_payload()['cards']}
        print(json.dumps({'test_frozen_legacy_confirmation_uses_fixture_day': day}))
        legacy_moves=['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4']
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('legacy-miss-game','fixture','fixture',?,'rapid',1,'white','loss',?,'[]')",(day,chess.STARTING_FEN))
            database.execute("INSERT INTO repertoire_decision_events_legacy(id,game_id,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) VALUES('legacy-miss-event','legacy-miss-game','fixture',?,1,'root','e2e4','d2d4','miss',?,?)",(legacy_id,day,day))
            miss_proposal={'card_id':legacy_id,'repertoire_id':'fixture','reason':order.MISS_REASON,
                'frozen_token':list(order.activation(database)),
                'study_route':{'generation':1,'line_id':'post-snapshot-legacy','decision_index':0,
                    'content':[chess.STARTING_FEN,json.dumps(legacy_moves),'white',chess.STARTING_FEN,json.dumps(legacy_moves)]}}
            assert order.proposal_valid(database,miss_proposal)
            assert refresh._admit_one_prioritized_opening(database,tail_day,miss_proposal)
        print(json.dumps({'legacy_runtime_proof':{'protected_unranked':legacy_id,'post_snapshot_fallback_admitted':fallback_id,'stale_proposal_refused':race_id,'display_and_issuance_keep_confirmed_attempt':legacy_id}}))
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,1)",(ids[0],day))
            queue_count_before_rollback=database.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0]
            order.rollback_run(database,run_id)
            assert len(order.restore_queue_changes(database,run_id)['queue_conflicts'])==2
            assert database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==1
            assert database.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0]==queue_count_before_rollback
        # A separately committed foreground change invalidates a staged publication.
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            stale={**plan,'run_id':'stale','checksum':'stale'}
            order.stage_run(database,stale,day);order.stage_ranks(database,stale,stale['ranks'])
        with psycopg.connect(url) as raw:raw.execute('UPDATE cards SET revision=revision+1 WHERE id=%s',(ids[1],))
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            try:order.activate_run(PostgresConnection(raw),stale)
            except ValueError as error:assert 'Stale plan' in str(error)
            else:raise AssertionError('Foreground mutation did not fence activation')
        # Static ranking stays an indexed page read at the observed backlog scale.
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            static={'run_id':'static-index-proof','checksum':'static-index-proof','ranks':[]}
            order.stage_run(database,static,day)
            with raw.cursor().copy('COPY frozen_opening_ranks(run_id,card_id,ordinal,fingerprint,memberships_json,definition_json) FROM STDIN') as copy:
                for ordinal in range(1,35401):
                    copy.write_row(('static-index-proof','fixture-'+str(ordinal),ordinal,'0'*64,'["fixture"]','{}'))
            raw.execute('ANALYZE frozen_opening_ranks')
            explained=database.execute_native("EXPLAIN (ANALYZE,FORMAT JSON) SELECT card_id,ordinal FROM frozen_opening_ranks WHERE run_id=%s AND ordinal>%s ORDER BY ordinal LIMIT 16",('static-index-proof',1000)).fetchone()[0][0]
            def nodes(plan):
                yield plan['Node Type']
                for child in plan.get('Plans',[]):yield from nodes(child)
            assert any('Index' in node for node in nodes(explained['Plan'])),explained
            assert explained['Plan']['Actual Rows']==16
            print(json.dumps({'static_rows':35400,'page_rows':16,'server_execution_ms':explained['Execution Time'],'plan_nodes':list(nodes(explained['Plan']))}))
        print(json.dumps({'result':'PASS','proofs':['schema migration','safe deferred-tail publication','strict incomplete-plan refusal','study-only rank paging','native scored and deferred admission','locked/invalid/changed tail fences','dedup/idempotency','scored and deferred queue refresh','issued-during-prepare fence','stale-content serving hold','read-only CLI capture','tail day boundary and restart','rollback preserves study','concurrent stale-plan guard'],'scored':plan['scored_count'],'deferred':plan['deferred_count'],'deferred_fraction':plan['deferred_fraction'],'deferred_rank':plan['deferred_cards'][0]['rank'],'seconds':round(time.monotonic()-started,2),'container':container,'volume':volume}))
    finally:
        try:
            diagnostic=subprocess.run(['docker','logs',container],text=True,capture_output=True)
            (ROOT/'test-results/frozen-postgres-container.log').write_text((diagnostic.stdout+diagnostic.stderr).replace(password,'<redacted>'))
        finally:
            close_pools()
            for kind,identity in [('container',container),('volume',volume)]:
                matches=command(['docker',kind,'ls',*(['--all'] if kind=='container' else []),
                                 '--filter','name=^'+('/' if kind=='container' else '')+identity+'$',
                                 '--format','{{.ID}}' if kind=='container' else '{{.Name}}']).split()
                for match in matches:
                    labels=json.loads(command(['docker',kind,'inspect',match,'--format',
                                               '{{json .Config.Labels}}' if kind=='container' else '{{json .Labels}}']))
                    assert labels.get('tempo.owner')==container and labels.get('tempo.disposable')=='true', 'Cleanup ownership mismatch'
                    command(['docker',kind,'rm',*(['--force'] if kind=='container' else []),match])
                assert not command(['docker',kind,'ls',*(['--all'] if kind=='container' else []),
                                    '--filter','name=^'+('/' if kind=='container' else '')+identity+'$',
                                    '--format','{{.ID}}' if kind=='container' else '{{.Name}}']), 'Owned resource survived cleanup'
            shutil.rmtree(credential_directory,ignore_errors=True)
        print('Task-owned disposable database removed',flush=True)


if __name__=='__main__':main()
