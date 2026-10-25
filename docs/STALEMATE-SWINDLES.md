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

## Retired offline generator command

The repository no longer includes the offline mining/build CLI, so the former reproduction and sample commands are not runnable from the current tree. The exact retired implementation is preserved at the immutable source revision [`cb0ed5910e6bd05894919fd8d49f5dc7bc236975`](https://github.com/aaweaver-actuary/tempo/blob/cb0ed5910e6bd05894919fd8d49f5dc7bc236975/scripts/stalemate_swindles.py). This pointer is historical source, not a supported command or a promise that the corpus can be regenerated with current checkout contents.

The checked-in bundle and manifest remain the source artifacts. The manifest records the published archive identity and checksum, completed scan counters, selection details, and bundle checksum; validate those immutable artifacts rather than treating a partial sample or a reconstructed scan as the September corpus.

## Determinism and selection

Candidate identity is SHA-256 of the four position-identity FEN fields, newline, and historical move UCI; halfmove/fullmove counters are ignored. Duplicate representatives prefer non-BOT, larger material deficit, higher minimum rating, slower speed (Classical/Rapid/Blitz/Bullet), and lexicographically smaller game ID. A final canonical-record comparison resolves otherwise identical rank ties independently of input order.

Motifs include defender terminal material class, king zone, terminal attacker queen/rook count buckets (0/1/2plus), and swindle kind. Chapter names describe material configuration; `pawns_only` does not independently assert every pawn is physically blocked. Candidates are ranked within each motif, capped, and selected round-robin across sorted motifs. The 40-per-motif cap remains in force during shortfalls. The manifest distinguishes insufficient unique candidates from additional shortages caused by caps.

Study/source/node/exercise IDs use a fixed UUIDv5 namespace, corpus ID, candidate key, role, and node path. All content timestamps are the source month's first day at UTC midnight. JSON key ordering, candidate ranking, chapters, and traversal are deterministic. A changed canonical bundle cannot overwrite an existing different bundle under the same ID; deliberately choose `v2` for a revision. Original transferable content remains separate from personal enrollment/review state.

## Validation and limitations

Synthetic tests use original White/Black positions, including a three-reply immediate forced stalemate and a single-legal-move exclusion. Run `make python-file FILE=backend/tests/test_stalemate_swindles.py`; its bounded checked-in corpus test validates all available real artifacts without rescanning the dump. The regular disposable PostgreSQL durability runner also imports/reimports generated synthetic content and each checked-in corpus, proving they stay draft and unscheduled.

There is no Stockfish/LLM analysis, probability model or non-stalemate control population, personalized/adaptive curriculum, automatic refresh, new web/background service, new schema/card/grader family, or modification of the live study database. Generator implementation and canonical corpus population are separately reportable outcomes: a failed full scan must be reported as incomplete and must never be replaced with synthetic examples labeled as the September corpus.
