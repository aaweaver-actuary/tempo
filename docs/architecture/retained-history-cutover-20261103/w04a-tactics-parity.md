# W04A Tactics parity boundary

Source baseline: `5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a`; W01 contract dependency: `20084356`. This is a source inventory, not a live-data census or a transfer specification. The dispositions below are provisional: all required families block cutover until source authority, complete bytes and references, target mapping, conflict behavior and final-fence reconciliation have native evidence under W01's contract.

| Legacy source and entire PK | Existing Learning target | Disposition / missing evidence |
| --- | --- | --- |
| `tactic_progress(puzzle_id)` | `learning.tactic_progress(puzzle_id)` | BLOCK until exact row, card reference, admission state and final watermark are transferred and verified. Same columns alone do not prove transfer. |
| `tactic_discovery_attempts(id)` | `learning.tactic_discovery_attempts(id)` | BLOCK until original receipt and command identity/replay binding are transferred and verified. Attempt ID, deck/puzzle, clean and original result JSON are required. |
| `tactic_captures(id)` | No corresponding Learning table in current migration manifest | BLOCK: preserve card reference, source kind/ref/URL/note, capture time and request/result JSON; requires coordinator-owned schema and capture command/receipt contract. |
| `tactic_pack_activation(pack_id)` | No corresponding Learning table | BLOCK: preserve original activation and catalog version/pack definition; coordinate schema and catalog owner. |
| `tactic_introductions(puzzle_id)` | No corresponding Learning table | BLOCK: preserve pack, introduction date and daily quota semantics; coordinate schema. |
| `tactic_rotation(id)` | No corresponding Learning table | BLOCK: preserve singleton cursor and rotation behavior; coordinate schema. |
| Tactic `cards(id)`, memberships, queue, reviews and issued attempts | Learning cards/queue/review owners | BLOCK pending their owner mappings and exact provenance, schedule, issued identity, offline journal and replay proof; do not regenerate historical grades. |
| Settings (legacy singleton `settings(id)`) | Learning `settings(id)` | BLOCK pending owner mapping of local day, quota and interval values and shared settings disposition. |
| Catalog assets and captured puzzle inputs (PGN, puzzle IDs, FEN and moves) | Current Learning puzzle validation accepts FEN/moves | BLOCK pending catalog version, pack membership and source/provenance evidence. A matching derived card ID alone is not evidence of same original identity. |

The focused `test_tactic_retained_progress_and_original_attempt_replay` seeds representative original progress and receipt rows directly in a disposable Learning database. It checks original receipt replay and unchanged original progress timestamps through a later solve. It does not exercise a transfer, verify an imported card schedule, validate original capture provenance, pack rotation, or prove F09 end to end. The W04B consumer cannot assume capture or catalog/pack APIs from this fixture; its only existing surface here is `POST /api/tactics/attempt` and `GET /api/tactics/progress`.
