# Stable chess-card identity

`tempo-chess-core==0.1.0` exports `canonical_fen_key(fen)` and
`card_id(starting_fen, moves)` from `tempo_chess_core`. Runtime dependencies are
standard library only; supported Python is >=3.12,<3.15, qualified on 3.12 and 3.14.

The FEN key joins the first four supplied whitespace-separated fields. Card IDs
hash UTF-8 `fen_key + '\n' + ' '.join(move.strip() for move in moves)` with SHA-256.
There is no FEN validation/repair, case folding, SAN/UCI conversion, or legal
en-passant normalization. Castling/en-passant distinctions and malformed inputs
retain the legacy persisted identity. This package implements stable identity,
not the future Rust/WASM/chess capability surface.

From this directory: `uv sync --locked --no-editable`,
`uv run --locked --no-editable python -m build`, and
`uv run --locked --no-editable python -m pytest tests`.

`tests/baseline.json` records 90 cases captured from the unchanged backend
algorithm at main `6b99085f8978346a959b5d4a6ea44b6e56f05dd6` before extraction.
The eight shared digest fixtures are copied unchanged into the artifact tests;
the backend adapter regression verifies that copy against the repository fixture.
Rust's card-ID contract is equivalent for those shared cases. Its position-search
FEN canonicalizer uses legal en-passant semantics and is a different API.
