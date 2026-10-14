"""Disposable PostgreSQL proof for frozen order: no live target accepted."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import psycopg
from app.postgres_store import PostgresConnection,tempo_row_factory,close_pools
from app.services import frozen_opening_order as order,postgres_queue_refresh as refresh
from app.services.frozen_maia_math import content_fingerprint
from app.services.cards import card_id
import chess


def command(arguments):
    return subprocess.run(arguments,text=True,capture_output=True,check=True).stdout.strip()


def main():
    suffix=secrets.token_hex(5);container='tempo-frozen-'+suffix;volume=container+'-data'
    password=secrets.token_urlsafe(24)
    started=time.monotonic()
    try:
        command(['docker','volume','create','--label','tempo.owner=frozen-maia-order',volume])
        command(['docker','run','-d','--name',container,'--label','tempo.owner=frozen-maia-order','-e','POSTGRES_PASSWORD='+password,'-p','127.0.0.1::5432','-v',volume+':/var/lib/postgresql','postgres:18.6-trixie'])
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
        os.environ['TEMPO_DATABASE_READ_URL']=url;os.environ['TEMPO_DATABASE_WRITE_URL']=url
        day='2026-10-14';ids=[]
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('fixture','Fixture','test',?)",(day,))
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('fixture',1,'ready','2026-10-14')")
            database.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            database.execute('UPDATE settings SET study_only_mode=1,new_cards_per_day=2 WHERE id=1')
            for index,move in enumerate(('e2e4','d2d4')):
                identifier=card_id(chess.STARTING_FEN,[move]);ids.append(identifier)
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,'fixture','prefix',?,?,'new',?,'opening','white')",(identifier,chess.STARTING_FEN,json.dumps([move]),day))
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'fixture','fixture','white',?,?,?)",(f'line-{index}',chess.STARTING_FEN,json.dumps([move]),day+str(index)))
                database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES('fixture',1,?,0,?,NULL,?, ?,?,'white','prefix',0,0,'[]')",(f'line-{index}',identifier,' '.join(chess.STARTING_FEN.split()[:4]),chess.STARTING_FEN,json.dumps([move])))
            ranks=[]
            for ordinal,identifier in enumerate(reversed(ids),1):
                card=dict(database.execute('SELECT * FROM cards WHERE id=?',(identifier,)).fetchone())
                ranks.append({'id':identifier,'rank':ordinal,'fingerprint':content_fingerprint(card,['fixture']),'memberships':['fixture']})
            plan={'run_id':'native-proof','checksum':'native-proof','ranks':ranks}
            order.stage_run(database,plan,day);order.stage_ranks(database,plan,ranks);order.activate_run(database,plan)
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
        assert not next_page['items']
        prepared=refresh._prepare_queue_randomization(day)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            assert refresh._publish_queue_randomization(PostgresConnection(raw),day,prepared)
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            restored=order.restore_queue_changes(database,'native-proof')
            assert len(restored['restored_queue_entries'])==2 and not restored['queue_conflicts']
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
            from app.services.queue_application import _AVAILABLE_ATTEMPT_SQL
            assert [row[0] for row in database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall()]==[database.execute('SELECT id FROM daily_queue WHERE card_id=?',(ids[1],)).fetchone()[0]]
            database.execute('UPDATE cards SET revision=1 WHERE id=?',(ids[0],))
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('changed-membership','changed','test',?)",(day,))
            database.execute("INSERT INTO repertoire_cards(card_id,repertoire_id) VALUES(?,'changed-membership')",(ids[0],))
            assert len(database.execute(_AVAILABLE_ATTEMPT_SQL,(day,)).fetchall())==1
            database.execute("DELETE FROM repertoire_cards WHERE repertoire_id='changed-membership'")
        # Read-only CLI snapshot uses the owning boundary and stable pagination.
        cli_spec=importlib.util.spec_from_file_location('frozen_cli',ROOT/'scripts/frozen-maia-order.py')
        cli=importlib.util.module_from_spec(cli_spec);cli_spec.loader.exec_module(cli)
        captured=cli.capture()
        assert len(captured['cards'])==2 and captured['metadata'][0]['schema_version']==48
        close_pools()
        command(['docker','restart',container])
        port=command(['docker','inspect',container,'--format','{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}'])
        url=f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
        os.environ['TEMPO_DATABASE_READ_URL']=url;os.environ['TEMPO_DATABASE_WRITE_URL']=url
        for attempt in range(100):
            try:
                with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
                    database=PostgresConnection(raw)
                    assert order.activation(database)[0]=='native-proof'
                    assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue ORDER BY position').fetchall()]==list(reversed(ids))
                break
            except psycopg.OperationalError:time.sleep(.1)
        else:raise RuntimeError('Restart proof timed out')
        with psycopg.connect(url,row_factory=tempo_row_factory) as raw:
            database=PostgresConnection(raw)
            database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,1)",(ids[0],day))
            order.rollback_run(database,'native-proof')
            assert len(order.restore_queue_changes(database,'native-proof')['queue_conflicts'])==2
            assert database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==1
            assert database.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0]==2
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
        print(json.dumps({'result':'PASS','proofs':['schema migration','study-only rank paging','native admission','dedup/idempotency','queue refresh','issued-during-prepare fence','stale-content serving hold','read-only CLI capture','day boundary','restart','rollback preserves study','concurrent stale-plan guard'],'seconds':round(time.monotonic()-started,2),'container':container,'volume':volume}))
    finally:
        diagnostic=subprocess.run(['docker','logs',container],text=True,capture_output=True)
        (ROOT/'test-results/frozen-postgres-container.log').write_text((diagnostic.stdout+diagnostic.stderr).replace(password,'<redacted>'))
        close_pools()
        subprocess.run(['docker','rm','-f',container],check=False,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','volume','rm',volume],check=False,stdout=subprocess.DEVNULL)
        print('Task-owned disposable database removed',flush=True)


if __name__=='__main__':main()
