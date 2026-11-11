# Minimal Game Review

The router prefix is `/api/product/game-review`. `GET /games` lists saved games;
`GET /games/{game_id}?repertoire_id=...` compares a saved game using its saved
color; `POST /compare` accepts `{pgn, repertoire_id, color}` for one pasted game.
Both comparison routes fully replay the game legally before reading current
authored lines, then call the pure `comparison.first_departure` with typed facts.
Results are `unavailable`, `coverage_end`, `departure`, or `covered`.

```python
import chess
from app.games.comparison import AuthoredLine, GameFacts, first_departure

start = chess.STARTING_FEN
game = GameFacts(start_fen=start, moves=('e2e4',))
lines = [AuthoredLine(start_fen=start, moves=('d2d4',))]
result = first_departure(game, lines)  # departure at ply 1
```

Storage reads are read-only; comparison neither saves pasted games nor contacts a
provider or mutates Study. Retired provider and feedback implementations are
historical, not prerequisites for this path.
