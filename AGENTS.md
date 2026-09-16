# Tempo working rules

Read `CONTRIBUTING.md` before changing behavior. Every user-raised defect, now and in future work, requires a specific named regression test in the regular suite before it can be closed. Record coverage in `tests/REGRESSIONS.md`. Never bypass or silently skip those tests to release a change.

Local Docker Tempo is the full product. SQLite is authoritative for cards, reviews, queues, repertoires, games, and sync metadata. A service failure must show an actionable error and never substitute sample records or false success. GitHub Pages is a clearly marked practice demo.

Keep coherent fixes in separate commits and preserve existing uncommitted work. Migrate deterministic logic toward Rust/WASM only after Python/Rust parity fixtures pass. Do not remove the Python compatibility path before parity.
