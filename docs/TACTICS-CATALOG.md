# Tactical puzzle catalog

Tempo packages 17,300 distinct puzzles from the Lichess open puzzle database into 692 packs of 25. The source export is CC0 and may be used and redistributed without restriction: https://database.lichess.org/#puzzles.

`public/data/tactics-catalog.json` is the authoritative, versioned manifest. It records the pinned export URL, retrieval date, SHA-256 checksum, pack ranges, and deterministic selection rule. The retired offline generation scripts are recoverable at [the last-good source snapshot](https://github.com/aaweaver-actuary/tempo/tree/8c0b3b5a22fd357607497ac4e50ad9804884c12c/scripts). Catalog regeneration is no longer a supported repository command. The existing `backend/tests/test_tactical_catalog.py` invariants validate all 692 complete packs, 17,300 unique legal puzzles and preservation of the original puzzle fields. Existing puzzle identities and source fields are preserved, with their former deck and position retained for migration.

General themes use fixed rating bands. Named mates use the 50 lowest and 50 highest rated eligible legal puzzles because rare patterns cannot reliably fill the general bands. Puzzle IDs are globally unique across the catalog.
