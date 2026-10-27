"""Native Analysis storage proof; actual cross-owner generation fencing is pending.

The current-fence capability uses a private test table and row reservation. It
is explicitly not the production Repertoire/settings/personal/evidence adapter.
Reuse the existing capped/labelled disposable fixture; no install or live DSN.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.analysis.jobs.ports import AnalysisFence
from tempo_legacy_database.adapters.analysis.publications import PostgresAnalysisPublications, SCHEMA_SQL
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from test_product_analysis_publications import prepared
from test_product_introductions_postgres import owned_postgres


@pytest.fixture
def native(owned_postgres):
    schema='analysis_'+uuid4().hex
    with psycopg.connect(**owned_postgres) as raw:
        raw.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        raw.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        raw.execute(SCHEMA_SQL,prepare=False)
        raw.execute('CREATE TABLE fixture_analysis_fence(repertoire_id text PRIMARY KEY,fence_json text NOT NULL)')
        raw.execute('INSERT INTO fixture_analysis_fence VALUES(%s,%s)',('rep',prepared().fence.model_dump_json()))
    failure={'commit':False}
    @contextmanager
    def transaction():
        with psycopg.connect(**owned_postgres,row_factory=tempo_row_factory) as raw:
            raw.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            raw.execute("SET LOCAL lock_timeout='3s'")
            raw.execute("SET LOCAL statement_timeout='5s'")
            yield PostgresConnection(raw)
            if failure['commit']:
                raise RuntimeError('Analysis disposable transaction rollback')
    def current(database,repertoire_id,*,reserve):
        row=database.execute('SELECT fence_json FROM fixture_analysis_fence WHERE repertoire_id=?'+
                             (' FOR UPDATE' if reserve else ''),(repertoire_id,)).fetchone()
        return AnalysisFence.model_validate_json(row[0])
    adapter=PostgresAnalysisPublications(transaction,current_fence=current)
    try:
        yield adapter,transaction,failure
    finally:
        with psycopg.connect(**owned_postgres) as raw:
            raw.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_native_analysis_cache_readback_replay_and_current_versus_historical_fence(native):
    adapter,transaction,_=native
    value=prepared()
    assert adapter.current_result('rep') is None and adapter.latest_result('rep') is None
    assert adapter.publish_if_current(value)
    assert adapter.current_result('rep')==value
    with transaction() as database:
        original=database.execute('SELECT xmin::text FROM analysis_product_publications').fetchone()[0]
    assert adapter.publish_if_current(value)
    with transaction() as database:
        assert database.execute('SELECT xmin::text FROM analysis_product_publications').fetchone()[0]==original
        database.execute('UPDATE fixture_analysis_fence SET fence_json=?',
                         (value.fence.model_copy(update={'settings_revision':2}).model_dump_json(),))
    assert not adapter.publish_if_current(value)
    assert adapter.current_result('rep') is None and adapter.latest_result('rep')==value


def test_native_analysis_concurrent_first_publication_has_one_unchanged_logical_row(native):
    adapter,transaction,_=native
    value=prepared();gate=Barrier(2)
    def publish():
        gate.wait(timeout=10)
        return adapter.publish_if_current(value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[future.result(timeout=15) for future in (pool.submit(publish),pool.submit(publish))]
    assert results==[True,True]
    with transaction() as database:
        assert database.execute('SELECT count(*) FROM analysis_product_publications').fetchone()[0]==1
        before=database.execute('SELECT xmin::text FROM analysis_product_publications').fetchone()[0]
    assert adapter.publish_if_current(value) and adapter.latest_result('rep')==value
    with transaction() as database:
        assert database.execute('SELECT xmin::text FROM analysis_product_publications').fetchone()[0]==before


def test_native_analysis_failed_commit_preserves_original_publication_and_releases_reservations(native):
    adapter,transaction,failure=native
    original=prepared();assert adapter.publish_if_current(original)
    current=original.fence.model_copy(update={'evidence_revision':2})
    with transaction() as database:
        database.execute('UPDATE fixture_analysis_fence SET fence_json=?',(current.model_dump_json(),))
    failure['commit']=True
    with pytest.raises(RuntimeError,match='Analysis disposable transaction rollback'):
        adapter.publish_if_current(replace(original,fence=current))
    failure['commit']=False
    assert adapter.latest_result('rep')==original and adapter.current_result('rep') is None
    assert adapter.publish_if_current(replace(original,fence=current))
    assert adapter.current_result('rep').fence==current
