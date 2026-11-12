# Pure chess support

Import the named module rather than a domain implementation through a facade.
The current modules are:

| Module | Supplied-input responsibility |
| --- | --- |
| [position_identity.py](position_identity.py) | FEN position identity normalization. |
| [cards.py](cards.py) | `card_id` from the shared `tempo_chess_core` owner. |
| [pgn.py](pgn.py) | PGN line/annotation parsing and trained-turn prefixes. |
| [opening_segments.py](opening_segments.py) | Opening identities and bounded presentation traversal. |

These helpers do not connect to databases, run providers or start engine workers.
The former evaluation, motifs and threat-geometry modules and defense contract
are absent; the old seven-module relocation count is historical, not current
inventory. This guide does not establish caller isolation or installed behavior.
