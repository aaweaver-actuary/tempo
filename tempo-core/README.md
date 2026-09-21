# Tempo core

Rust/WASM deterministic chess and scheduling primitives live here. The
TypeScript adapter is `app/lib/tempo-core.ts`, and parity fixtures live under
`tests/fixtures`.

Build through `npm run build:wasm` or run Rust checks directly with Cargo. Keep
the Python compatibility path until shared fixtures prove parity; do not move
network, browser storage, or UI orchestration into this crate.
