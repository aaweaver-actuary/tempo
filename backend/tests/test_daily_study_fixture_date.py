"""Browser backlog fixtures must target the product's published local day."""
from contextlib import contextmanager
from datetime import date
import importlib.util
from pathlib import Path


def test_issue108_daily_study_fixture_uses_authoritative_date_across_utc_midnight(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/check_postgres_daily_study_dispatch.py'
    specification = importlib.util.spec_from_file_location('daily_study_date_proof', path)
    proof = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(proof)

    class UtcContainerDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 9)

    statements = []
    refresh_dates = []

    class FixtureDatabase:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))

        def commit(self):
            pass

    @contextmanager
    def fixture_connection(*args, **kwargs):
        yield FixtureDatabase()

    monkeypatch.setattr(proof, 'date', UtcContainerDate)
    monkeypatch.setenv('TEMPO_DAILY_STUDY_QUEUE_DATE', '2026-10-08')
    monkeypatch.setattr(proof.psycopg, 'connect', fixture_connection)
    monkeypatch.setattr(proof.postgres_store, 'connection', fixture_connection)
    monkeypatch.setattr(proof, 'request_queue_refresh_in_transaction',
                        lambda database, queue_date: refresh_dates.append(queue_date))
    proof.seed('daily-study-proof-date-boundary', request_refresh=True)

    assert refresh_dates == ['2026-10-08']
    locked_card_dates = [parameters[3] for statement, parameters in statements
                         if 'generate_series' in statement and 'INSERT INTO cards' in statement]
    assert locked_card_dates == ['2026-10-08']
