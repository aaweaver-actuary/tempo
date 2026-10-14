# Chess-line package

Real canonical FEN/move and SAN behavior extracted from the frontend. Public ESM entry: `@tempo/chess-line`. Run `npm ci --workspaces=false`, `npm run build`, then `npm test` from this directory. No Python, Rust/WASM, browser, database or model dependency.

The explicit `--workspaces=false` prevents npm setup inside this root from installing the legacy workspace dependency tree.
