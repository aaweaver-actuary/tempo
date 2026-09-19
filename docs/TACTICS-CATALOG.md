# Tactical puzzle catalog

Tempo packages 17,300 distinct puzzles from the Lichess open puzzle database into 692 packs of 25. The source export is CC0 and may be used and redistributed without restriction: https://database.lichess.org/#puzzles.

`public/data/tactics-catalog.json` is the authoritative, versioned manifest. It records the pinned export URL, retrieval date, SHA-256 checksum, pack ranges, and deterministic selection rule. `scripts/expand_tactics_catalog.py` validates every move and fails if any pack is incomplete. Existing puzzle identities and source fields are preserved, with their former deck and position retained for migration.

General themes use fixed rating bands. Named mates use the 50 lowest and 50 highest rated eligible legal puzzles because rare patterns cannot reliably fill the general bands. Puzzle IDs are globally unique across the catalog.
