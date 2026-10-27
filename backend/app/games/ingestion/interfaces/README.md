# Retained Games ingestion interface

Owner: #491, `game-ingestion` in allocation revision 2.

`normalize_provider_pgn` validates one supplied legal, completed game for an
explicit account and returns the existing Games `GameRecord` from `games/records.py`.
`RejectedGame` retains validation failure identity. This record is distinct from
the product acquisition wire record; neither type is converted or duplicated.
`serialize_job` projects an already-supplied saved job and parses result JSON
only for a completed job. None of these functions acquires or writes a database.

Imports depend on chess and Python values only. No provider, connection, worker,
Study policy or scheduling effect starts on import. Callers own acquisition and
transactions; Games storage owns imported rows. Existing implementation modules
remain private and unchanged during this mechanical surface checkpoint.

Old import paths remain until owned callers and #501's cross-owner caller patch
are migrated; #482 validates the final import/export map. Identity, no-I/O cold
imports and actual normalization/projection use are checked in
`backend/tests/test_public_surface_game_ingestion.py`.
