"""Legacy call-site composition for authored repertoire conflict detection."""
from tempo_legacy_database.adapters.repertoire_lines import line_snapshot as _line_snapshot
from app.studies.repertoire_conflicts import (
    trained_move_index_from_snapshot as _trained_move_index_from_snapshot,
    conflicts_from_snapshot as _conflicts_from_snapshot,
)


def trained_move_index(database, repertoire_id: str | None = None) -> dict:
    return _trained_move_index_from_snapshot(_line_snapshot(database, repertoire_id))


def find_repertoire_conflicts(database, repertoire_id: str | None = None) -> list[dict]:
    return list(_conflicts_from_snapshot(_line_snapshot(database, repertoire_id)))
