# Shared fixtures

Fixtures here are cross-runtime contracts, especially Python/Rust parity for
canonical lines, positions, and motifs. A fixture change is an API/behavior
change and should include the corresponding tests and regression documentation.

`card-id-parity.json` locks the Python persistence identity used by Rust's
`card_id`: hash the first four supplied FEN fields and trimmed moves. This is
deliberately distinct from position-search FEN canonicalization, which drops
an en-passant square when capture is unavailable.
