# Exact engine sources and notices

Unmodified JavaScript/WASM: @lichess-org/stockfish-web 0.5.0.
Npm tarball: https://registry.npmjs.org/@lichess-org/stockfish-web/-/stockfish-web-0.5.0.tgz
Integrity: sha512-4p+bnJKr+ufCanyaqUFJkvajVf443sg51+OPnDKjAi91kiY8DGAbfjj5WlmWybwILOF7ouPEjeCYwlPUV3EU3g==
Corresponding source and build scripts:
https://github.com/lichess-org/stockfish-web/tree/931c33470b0c80756c99dfb05008d951aaa8f751
Stockfish base:
https://github.com/official-stockfish/Stockfish/tree/edb0d9db6731067ec50ce619ff372b463bc4dd5d
Apply, in order, both sf_19_smallnet patches named in engine-descriptor.json from
that exact stockfish-web source. Its build.py specifies Emscripten 5.0.7 and
compiler/linker options; src contains the JS/C++ interface glue. No local engine
source changes were made. Source authors/copyright notices are retained there.

The upstream npm manifest declares AGPL-3.0-or-later while the distributed LICENSE
is GNU GPL v3. Both facts are recorded, not silently reconciled. The shipped
LICENSE is copied unchanged as stockfish-web-LICENSE.txt. Source is publicly
available at the exact references above. This package claims no relicensing.

NNUE bytes: https://tests.stockfishchess.org/api/nn/nn-61e7af4bb97d.nnue
SHA-256: 61e7af4bb97d51eeeb25d322916f86513b5cd3a827ce189c98c6e31946f99e5b
Stockfish acknowledgement of training-data provenance and applicable data license:
https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/README.md

Runtime dependencies are locked independently. AJV is MIT; chess.js is BSD-2-Clause.
Their licenses accompany the installed dependency packages. No engine/node module
is substituted from a system-wide installation.
