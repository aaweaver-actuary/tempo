# Repertoire scope and source storage

These adapters contain fixed scope facts and saved-line projections on the caller's transaction.

| Module | Responsibility |
| --- | --- |
| [freshness.py](freshness.py) | Game-scope generation and historical coverage/opportunity predicates still used by schema initialization. |
| [move_verification.py](move_verification.py) | SQLite source aggregate, native PostgreSQL source token and bounded saved-line reads for move verification. |

[Move verification](../../../../backend/app/services/repertoire_move_verification.py) retains legal-move validation, chess replay, deadlines, source cursor and final source recheck. Source-token formats and dialect behavior remain unchanged.

The automatic repertoire-to-Games refresh adapter and its application service are removed. Authoring mutations no longer start a global game derivation sweep. The small preview lease probe now lives with canonical-prefix preview storage, and repertoire existence reads live in `adapters/repertoire_lines.py`.

Stored schemas, views and data remain unchanged. Historical freshness predicates are still required by the existing schema bootstrap; retaining them is not a live Coverage or Discovery worker registration.
