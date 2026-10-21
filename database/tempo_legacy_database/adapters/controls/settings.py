"""Settings facts on the caller's connection; admission and HTTP decisions stay outside."""


def effective_opening_limit_rows(database):
    return database.execute(
        'SELECT r.id,COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
        'FROM repertoires r CROSS JOIN settings s WHERE s.id=1'
    ).fetchall()


def repertoire_limit_row(database, repertoire_id: str):
    return database.execute(
        'SELECT r.new_cards_per_day,COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
        'FROM repertoires r CROSS JOIN settings s WHERE r.id=? AND s.id=1',
        (repertoire_id,),
    ).fetchone()


def study_mode_setting(database):
    return database.execute('SELECT study_only_mode FROM settings WHERE id=1').fetchone()
