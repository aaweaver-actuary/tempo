# Shared fixtures

Fixtures here are cross-runtime contracts, especially Python/Rust parity for
canonical lines, positions, and motifs. A fixture change is an API/behavior
change and should include the corresponding tests and regression documentation.

`card-id-parity.json` locks the Python persistence identity used by Rust's
`card_id`: hash the first four supplied FEN fields and trimmed moves. This is
deliberately distinct from position-search FEN canonicalization, which drops
an en-passant square when capture is unavailable.

## Chess operation contracts (source baseline e31e3460)

These operations have different owners and are not interchangeable:

| Operation | Owner and retained semantics |
| --- | --- |
| Persisted card ID | `tempo_chess_core.card_id` and `tempo-core::card_id_native` hash the first four **supplied** whitespace-normalized FEN fields and trimmed moves. Clocks do not matter; castling and even unusable en-passant fields do. Invalid FEN is hashed rather than repaired. |
| TypeScript exact key | `@tempo/chess-line`'s `canonicalFenKey` also takes the first four supplied fields without legal normalization. The alternative snapshot indexes `Chess(...).fen()` from `indexRepertoirePositions`, and lookup now canonicalizes its query through the same legal board representation before applying the raw key. Study's initial card state may preserve an unnormalized starting FEN; this prevents a pinned or irrelevant en-passant field from hiding a saved alternate, without changing persisted identity. |
| Rust legal position key | `tempo-core::canonical_fen_key_native` parses the position and emits four fields with legally available en-passant. It is not the persisted ID key or the raw TypeScript exact-key function. |
| Similarity distance | TypeScript `chessPositionDistance` and Rust `position_distance_native` require compatible turn, castling, legally available en-passant, and material; distance counts relocated squares within piece/color groups. Clocks are ignored. Invalid positions yield no distance. The shared distance fixture covers legal, irrelevant, and pinned en-passant and castling. |
| Legal line | TypeScript `canonicalizeMoves` accepts SAN or UCI; Rust `validate_uci_line_native` accepts UCI. Both stop at invalid, illegal or null moves and return diagnostics, but their input and diagnostic text contracts are not identical. Existing core fixtures cover nonstandard start, castling, en-passant, illegal moves and null moves. |
| Ranked matches | TypeScript's optional Study computation filters unavailable distances and distances above two, requires a next move only for similarity, sorts by distance then ply with stable ties, deduplicates by FEN and next move, and takes the requested limit (default eight). This is not the exact membership or persisted ID contract. |

These shared fixtures demonstrate the cases they enumerate; they do not certify
all chess positions, cross-language scheduling, or deployed worker behavior.
