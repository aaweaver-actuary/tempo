# Core source

`lib.rs` contains the exported deterministic Rust/WASM functions. Public
exports should remain small, serializable, and covered by shared Python/Rust
fixtures before frontend callers are migrated.
