"""Read-only authored line facts on the caller's database/transaction."""

def line_snapshot(database, repertoire_id: str | None) -> tuple[tuple[str, ...], ...]:
    parameters: tuple[str, ...] = ()
    where = ""
    if repertoire_id:
        where = "WHERE repertoire_id=?"
        parameters = (repertoire_id,)
    rows = database.execute(
        f"SELECT id,repertoire_id,name,trained_color,start_fen,moves_json "
        f"FROM repertoire_lines {where} ORDER BY id",
        parameters,
    ).fetchall()
    return tuple(tuple(row[column] for column in (
        "id", "repertoire_id", "name", "trained_color", "start_fen", "moves_json"
    )) for row in rows)
