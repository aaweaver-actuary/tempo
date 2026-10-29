# Game ingestion interface

Owner: #491, `game-ingestion` in allocation revision 2.

`PgnGameIngester.ingest` accepts one explicit `GameInput` and returns the existing
`CanonicalGame`. `BytesInput`, bounded `FileInput`, `normalize_pgn` and
`MAX_SOURCE_BYTES` retain their existing identities and behavior. Reading a file
occurs only when its supplied input is consumed; importing starts nothing.

Dependencies are neutral chess and the existing game-review wire values.
Canonical values remain owned by `tempo_game_review_contracts`; no DTO is copied
or foreign implementation re-exported. Providers, comparison, databases, Study,
queue mutation and workers are outside this surface.

The old package exports remain during caller migration. Remove those aliases
only after #501 has integrated cross-owner imports and #482's guard passes.
Source export identity, cold no-I/O import and actual PGN behavior are checked by
`backend/tests/test_public_surface_game_ingestion.py`; installed packaging is
separate and remains #501-owned.
