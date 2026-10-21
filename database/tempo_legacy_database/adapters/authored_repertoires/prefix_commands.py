"""Fixed canonical-prefix command queries on the caller's transaction."""

from ..projections.repertoire_statistics import repertoire_presence
from ..repertoire_scope.game_refresh import postgres_is_configured


def prefix_preview(database, preview_id, repertoire_id):
    return database.execute(
        "SELECT * FROM canonical_prefix_previews WHERE id=? AND repertoire_id=?",
        (preview_id, repertoire_id),
    ).fetchone()


def set_empty_prefix_preview(database, preview_id, repertoire_id):
    database.execute("UPDATE repertoires SET canonical_prefix_preview_id=? WHERE id=?",
                         (preview_id, repertoire_id))


def save_prefix_values(database, moves_json, revision, preview_id, repertoire_id):
    database.execute(
        "UPDATE repertoires SET canonical_prefix_moves_json=?,canonical_prefix_revision=?,canonical_prefix_preview_id=? WHERE id=?",
        (moves_json, revision, preview_id, repertoire_id),
    )


def fail_active_local_coverage(database, repertoire_id):
    database.execute("UPDATE repertoire_coverage_runs SET status='failed',last_error='Opening scope changed; refresh coverage' WHERE repertoire_id=? AND status IN ('queued','running')", (repertoire_id,))


def advance_priority_source_epoch(database, repertoire_id):
    database.execute("INSERT INTO priority_repertoire_source_epochs(repertoire_id,version) VALUES(?,1) "
                         "ON CONFLICT(repertoire_id) DO UPDATE SET version=priority_repertoire_source_epochs.version+1", (repertoire_id,))


def delete_priority_publication(database, repertoire_id):
    database.execute("DELETE FROM repertoire_priority_publications WHERE repertoire_id=?", (repertoire_id,))


def delete_introduction_priorities(database, repertoire_id):
    database.execute("DELETE FROM repertoire_card_introduction_priorities WHERE repertoire_id=?", (repertoire_id,))
