"""Private Study ranking/allocation facts. Borrow the current writer transaction.

No queue/schedule writer, connection factory, automatic schema install or commit.
SCHEMA_SQL is the bounded proposed schema for integration-owned migration
allocation; the native contract installs it only in its disposable database.
"""
import hashlib
import json

from app.contracts.product_v02.analysis import RankingOutput
from app.study.introductions.policy import RankingContext
from app.study.introductions.ports import AllocationState, Receipt

SCHEMA_SQL = """
CREATE TABLE study_product_introduction_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    rotation_after TEXT
);
INSERT INTO study_product_introduction_state(singleton) VALUES(1);
CREATE TABLE study_product_introduction_periods (
    study_day TEXT PRIMARY KEY,
    rotation_after TEXT
);
CREATE TABLE study_product_introduction_charges (
    presentation_id TEXT PRIMARY KEY,
    study_day TEXT NOT NULL REFERENCES study_product_introduction_periods(study_day),
    repertoire_id TEXT NOT NULL
);
CREATE INDEX study_product_introduction_charges_day ON study_product_introduction_charges(study_day);
CREATE TABLE study_product_introduction_rankings (
    context_key TEXT PRIMARY KEY,
    context_json TEXT NOT NULL,
    ranking_json TEXT NOT NULL
);
"""


def _context_json(context: RankingContext):
    return json.dumps([context.repertoires, context.settings_revision], separators=(",", ":"), ensure_ascii=False)


def _key(context):
    return hashlib.sha256(_context_json(context).encode()).hexdigest()


class PostgresIntroductionStore:
    """Accepts current PostgresConnection, using its parameter-bound SQL adapter."""
    def __init__(self, database):
        self.database = database

    def lock_allocation(self):
        row = self.database.execute(
            "SELECT singleton FROM study_product_introduction_state WHERE singleton=1 FOR UPDATE", ()).fetchone()
        if row is None:
            raise RuntimeError("Study introduction schema is not initialized")

    def state(self, day):
        cursor = self.database.execute(
            "SELECT rotation_after FROM study_product_introduction_state WHERE singleton=1", ()).fetchone()
        period = self.database.execute(
            "SELECT rotation_after FROM study_product_introduction_periods WHERE study_day=?", (day,)).fetchone()
        charges = self.database.execute(
            "SELECT presentation_id,study_day,repertoire_id FROM study_product_introduction_charges WHERE study_day=? ORDER BY presentation_id",
            (day,)).fetchall()
        return AllocationState(cursor[0] if cursor else None, period is not None,
                               period[0] if period else None, tuple(Receipt(*row) for row in charges))

    def receipt(self, presentation_id):
        row = self.database.execute(
            "SELECT presentation_id,study_day,repertoire_id FROM study_product_introduction_charges WHERE presentation_id=?",
            (presentation_id,)).fetchone()
        return Receipt(*row) if row else None

    def record_introduction(self, receipt, *, period_rotation_after):
        self.database.execute(
            "INSERT INTO study_product_introduction_periods(study_day,rotation_after) VALUES(?,?) ON CONFLICT(study_day) DO NOTHING",
            (receipt.day, period_rotation_after))
        self.database.execute(
            "INSERT INTO study_product_introduction_charges(presentation_id,study_day,repertoire_id) VALUES(?,?,?)",
            (receipt.presentation_id, receipt.day, receipt.repertoire_id))
        self.database.execute(
            "UPDATE study_product_introduction_state SET rotation_after=? WHERE singleton=1", (receipt.repertoire_id,))

    def saved_ranking(self, context):
        row = self.database.execute(
            "SELECT context_json,ranking_json FROM study_product_introduction_rankings WHERE context_key=?", (_key(context),)).fetchone()
        if row is None or row[0] != _context_json(context):
            return None
        try:
            return RankingOutput.model_validate_json(row[1])
        except ValueError:
            # An obsolete/invalid optional value cannot make Study unavailable.
            # Keep the durable row for diagnosis; serving uses the owner order.
            return None

    def save_ranking(self, context, ranking):
        self.database.execute(
            "INSERT INTO study_product_introduction_rankings(context_key,context_json,ranking_json) VALUES(?,?,?) "
            "ON CONFLICT(context_key) DO UPDATE SET context_json=excluded.context_json,ranking_json=excluded.ranking_json",
            (_key(context), _context_json(context), ranking.model_dump_json()))
