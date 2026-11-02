# Synthetic position Study fixtures

These fixtures are authored, legal UCI routes for isolated tests. They are synthetic and must never seed a live Study, user history, database, or review queue. Names such as `rep-italian` and `italian-knight-attack` are fixture-local labels, not persistent IDs.

`backend/tests/fixtures/position_study_cases.py` supplies immutable `Route`, `Frame`, `Decision`, and `EvidenceCase` values. Routes are replayed legally when the module is loaded. Each frame keeps its full FEN (including clocks and FEN en-passant field) plus `tempo_evidence_math.opening_position_key`, the existing four-field canonical exact-position key with legal en-passant semantics. Decision values keep expected UCI and a fixture policy version separately from the position key; a consumer should apply repertoire and standard-variant scope when forming scheduling identity. This fixture module does not declare a product identity or persistence schema.

Coverage includes the shared Italian 3.Bc4 trunk with both ...Nf6 and ...Bc5 opponent orders, two legal continuations after 4.Ng5, London transposition by alternate opponent move orders, a same-position cross-repertoire pair, legal en-passant and castling-right distinctions, equal positions with different clocks, and a legal selected-response conflict. Evidence scenarios cover due Good, nondue observation, a durable first error before terminal completion, a fresh wrong response after Good, and duplicate offline receipt suppression. The malformed UCI example is diagnostic input only and is intentionally excluded from the legal route set.

Run the focused owning test with:

```sh
make python-file FILE=backend/tests/test_position_study_fixture_legality.py
```

This check is pure and uses no database, service, engine, browser, or production code path.
