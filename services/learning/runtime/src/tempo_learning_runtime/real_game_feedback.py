"""Retained Learning obligations; never reads source games or evidence tables."""
MISS_REASON = "Priority review · missed in a recent game"
def clear_satisfied_miss_priority(database, card_id):
    database.execute("UPDATE learning_obligations SET satisfied_at=CURRENT_TIMESTAMP WHERE card_id=? AND satisfied_at IS NULL AND kind='real_game_miss' AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=? AND r.source_kind='study' AND r.invalidated_at IS NULL AND r.rating='correct' AND r.guided=0 AND CAST(r.reviewed_at AS TIMESTAMPTZ)>learning_obligations.observed_at)",(card_id,card_id))
    if not database.execute("SELECT 1 FROM learning_obligations WHERE card_id=? AND kind='real_game_miss' AND satisfied_at IS NULL LIMIT 1",(card_id,)).fetchone():
        database.execute("UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE card_id=? AND gameplay_priority_reason=?",(card_id,MISS_REASON))
