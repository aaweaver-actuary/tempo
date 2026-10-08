# Stalemate Swindles

These examples show moves that successfully produced stalemate opportunities in historical games. Except for the explicitly detected immediate-forced subset, they are not claims that the move was objectively best or that a correct opponent could not avoid the draw.

Corpus distributions describe observed stalemates, not the probability that a position will end in stalemate.

## September 2026 corpus

The checked-in [Study bundle](../public/data/studies/stalemate-swindles-2026-09-v1.tempo-study.json) contains 300 exercises. Its [manifest](../public/data/studies/stalemate-swindles-2026-09-v1.manifest.json) records the complete, checksum-verified scan of all 89,616,462 September games: 340,842 terminal stalemates among header-eligible parsed draws, 156,637 eligible candidates, and 137,013 unique candidates. There were no parsing errors or selection shortfalls.

The selected examples comprise 102 Lone king, 102 Pawns only, 35 Piece only, and 61 Mixed material exercises; 189 historical traps and 111 immediate-forced cases. King zones are 101 corner, 107 edge, and 92 interior. Speeds are 206 Blitz, 84 Rapid, and 10 Classical. The manifest includes rating and material-deficit distributions and all exclusion counters. These are selected-example distributions, not population rates.

The exact bundle-byte SHA-256 is:

```text
d8fc17a79bcc5d348e9643c44737bae1958f7c284fdd543cd840c79f1d373ff1
```

## Training and import

The puzzle begins **two plies before stalemate**, with the eventual stalemated defender to move. Its single reference answer is the defender's historical trap-setting move. The opponent's historical reply is retained as the second move of the Study source tree, and the explanation shows both moves in SAN.

An `immediate_forced_stalemate` example verifies that every legal opponent reply after the reference move immediately produces stalemate. A `historical_trap` example verifies the source-game reply but makes no forcing or uniqueness claim. Neither classification is an engine/tablebase proof of optimal play.

Every exercise is an ordinary Study `move_line`, in `single` mode with `open_judgment`: the historical move is correct; another legal move is unrecognized and can be self-assessed after reveal; an illegal move is invalid. The learner never has to enter the opponent reply. Promotion positions offer the existing Queen/Rook/Bishop/Knight control for board input.

Open **Studies**, select its existing `.tempo-study.json` import control, and choose the generated bundle. Browse **Lone king**, **Pawns only**, **Piece only**, or **Mixed material**. Imported exercises are drafts and can be practiced immediately. Explicitly enroll chosen exercises through the normal Study controls to use the daily queue, FSRS, sibling burial, and offline reviews. No exercises are enrolled automatically and no personal review history is transferred.

## Source and filters

Source: [Lichess standard-rated monthly export](https://database.lichess.org/), released under CC0. The September 2026 archive is `lichess_db_standard_rated_2026-09.pgn.zst`, listed as 89,616,462 games and 29.2 GB compressed. Its [published SHA-256](https://database.lichess.org/standard/sha256sums.txt) is:

```text
1de8eb64fed443f4be310c779e8d60a13bcfc6e3b104bcb39a018eb056edec89
```

Recheck the published checksum before reproducing a canonical run. The manifest records the actual verified compressed-byte checksum, source URL/filename, scan counters, filters, selection, distributions, and exact bundle-byte checksum. Source game URLs retain provenance; account names are excluded from generated candidate records and mini-PGNs.

Defaults require a draw header, a valid terminal stalemate, at least two plies, at least two defender legal moves, both ratings at least 1000, and a defender material deficit of at least 5 points at the root. Values are pawn 1, knight/bishop 3, rook 5, queen 9, king 0. Material deficit is a practical filter, not proof of an objectively lost position. BOT titles, Bullet, nonstandard variants, and explicit non-normal termination are excluded. Blitz, Rapid, and Classical are accepted. Headers are filtered before chess replay; noncandidates do not create game trees.

## Reproduce the canonical corpus

From an isolated checkout, install only the documented Python dependencies (`uv sync` in `backend/`, then `uv pip install --python .venv/bin/python -r requirements.txt`). The verified streaming mode additionally needs command-line `curl` and `zstd`; it does not add a Python Zstandard dependency. Keep raw dumps and intermediate files outside tracked source. This mode works without enough disk space to retain the archive:

```bash
nice -n 10 backend/.venv/bin/python scripts/stalemate_swindles.py mine \
  --source-url https://database.lichess.org/standard/lichess_db_standard_rated_2026-09.pgn.zst \
  --source-sha256 1de8eb64fed443f4be310c779e8d60a13bcfc6e3b104bcb39a018eb056edec89 \
  --source-month 2026-09 \
  --output test-results/stalemate-swindles/2026-09.candidates.jsonl \
&& backend/.venv/bin/python scripts/stalemate_swindles.py build \
  --candidates test-results/stalemate-swindles/2026-09.candidates.jsonl \
  --corpus-id lichess-standard-2026-09-v1 \
  --title "Stalemate Swindles" \
  --max-puzzles 300 --max-per-motif 40 \
  --output public/data/studies/stalemate-swindles-2026-09-v1.tempo-study.json \
  --manifest public/data/studies/stalemate-swindles-2026-09-v1.manifest.json
```

The miner owns one bounded archive pump and its download/decompression subprocesses. It verifies their exit statuses and the entire compressed-byte checksum before publishing complete output. It does not contact the Tempo application, an engine, or any database. Run only one corpus scan at a time; use the low-priority command to protect foreground study.

Reconcile `games_scanned` with the official monthly count before checking in the canonical artifact. `terminal_stalemates` counts stalemates among header-eligible parsed draws; BOT/speed/rating/termination-rejected draws are not replayed. It is not a count of every stalemate in every game in the archive. `draw_games_prefiltered` counts observed draw headers before header-quality rejection; `draw_games_parsed` counts successfully replayed eligible draw records. Exclusion counters describe the first rejecting filter. Malformed records increment `parse_errors` unless `--strict` requests immediate failure.

The build consumes exactly one mined JSONL path following `--candidates`, with its adjacent `PATH.metadata.json`. It checks the candidate-file hash and recomputes candidate chess facts. Repeated paths and byte-identical copies remain rejected. Version 1 receipts record source identity/checksum, month, filters, limits, completion, mining counters, and the candidate-file checksum, but contain no verifiable source-record ranges or partition identities. Different candidate-file hashes or disjoint candidate identities cannot establish disjoint scans: overlapping records may produce no candidate. Consequently, multiple receipts are rejected before publication, even when their source, month, and filters agree or their candidates appear disjoint. Mine one input and build from that output; do not concatenate separate candidate outputs or add their receipts' counters.

Mining counters are copied unchanged from the accepted receipt. `games_scanned` counts observed records; parsed-draw, terminal-stalemate, exclusion, error, and `eligible_candidates` counters describe mining observations before deduplication. `unique_candidates`, `selected_puzzles`, and shortfalls are computed separately from validated candidate identities and deterministic selection. The complete September corpus uses one receipt and is unchanged by this restriction.

Bundle and manifest files are staged before publication, with the manifest written last. Consumers must check its bundle hash; absent/mismatched metadata never certifies complete work. Failed commands preserve previous final files and remove task-owned temporary partial files. Hard process termination can leave explicitly `.partial` diagnostic files, which are not build inputs.

## Samples and plain PGN

These supported inputs do not by themselves verify the original compressed archive:

```bash
zstdcat lichess_db_standard_rated_2026-09.pgn.zst \
  | backend/.venv/bin/python scripts/stalemate_swindles.py mine \
      --source-month 2026-09 --output test-results/stalemate-swindles/plain.candidates.jsonl

backend/.venv/bin/python scripts/stalemate_swindles.py mine \
  --input sample.pgn --source-month 2026-09 \
  --output test-results/stalemate-swindles/sample.candidates.jsonl
```

`--max-games N` and `--stop-after-candidates N` always mark sampling output incomplete, even if the input also reaches EOF. Use a distinct sample corpus ID such as `lichess-standard-2026-09-smoke-v1` and paths under `test-results/` when building a sample. Canonical `lichess-standard-YYYY-MM-vN` IDs require complete verified archive input. Plain-stdin EOF cannot prove that an upstream download or decompressor succeeded.

Other mining controls: `--min-material-deficit`, `--min-rating`, comma-separated `--speeds` (including optional Bullet), `--include-bots`, `--progress-every` (default one million), and `--strict`. Progress goes to stderr; candidates are written incrementally to the output file. No partial-stream resume is implemented: restart the verified stream after a failure; an HTTP range is insufficient to restore decompression, framing, and full-source hashing safely.

## Determinism and selection

Candidate identity is SHA-256 of the four position-identity FEN fields, newline, and historical move UCI; halfmove/fullmove counters are ignored. Duplicate representatives prefer non-BOT, larger material deficit, higher minimum rating, slower speed (Classical/Rapid/Blitz/Bullet), and lexicographically smaller game ID. A final canonical-record comparison resolves otherwise identical rank ties independently of input order.

Motifs include defender terminal material class, king zone, terminal attacker queen/rook count buckets (0/1/2plus), and swindle kind. Chapter names describe material configuration; `pawns_only` does not independently assert every pawn is physically blocked. Candidates are ranked within each motif, capped, and selected round-robin across sorted motifs. The 40-per-motif cap remains in force during shortfalls. The manifest distinguishes insufficient unique candidates from additional shortages caused by caps.

Study/source/node/exercise IDs use a fixed UUIDv5 namespace, corpus ID, candidate key, role, and node path. All content timestamps are the source month's first day at UTC midnight. JSON key ordering, candidate ranking, chapters, and traversal are deterministic. A changed canonical bundle cannot overwrite an existing different bundle under the same ID; deliberately choose `v2` for a revision. Original transferable content remains separate from personal enrollment/review state.

## Validation and limitations

Synthetic tests use original White/Black positions, including a three-reply immediate forced stalemate and a single-legal-move exclusion. Run `make python-file FILE=backend/tests/test_stalemate_swindles.py`; its bounded checked-in corpus test validates all available real artifacts without rescanning the dump. The regular disposable PostgreSQL durability runner also imports/reimports generated synthetic content and each checked-in corpus, proving they stay draft and unscheduled.

There is no Stockfish/LLM analysis, probability model or non-stalemate control population, personalized/adaptive curriculum, automatic refresh, new web/background service, new schema/card/grader family, or modification of the live study database. Generator implementation and canonical corpus population are separately reportable outcomes: a failed full scan must be reported as incomplete and must never be replaced with synthetic examples labeled as the September corpus.
