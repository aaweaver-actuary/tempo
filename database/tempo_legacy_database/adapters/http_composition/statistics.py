"""Fixed legacy Games projection/mutation queries; application decisions stay outside."""


def daily_insights(database, status):
    return database.execute(
        "SELECT * FROM daily_chess_insights WHERE status=? ORDER BY local_day DESC,kind",
        (status,),
    ).fetchall()
