"""Current product settings read; retired settings remain stored unchanged."""
def public_settings(database):
    return database.execute(
        "SELECT tactics_new_per_day,study_only_mode,initial_depth,timezone,new_cards_per_day,study_new_per_day,light_first_interval_days FROM settings WHERE id=1",
    ).fetchone()
