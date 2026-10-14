# Chess-line regression ownership

Owning regular suite: `npm test` against compiled public exports.

`null and malformed moves remain raw diagnostics while valid moves are canonical` retains its title from `tests/unit/domain-boundary-regressions.test.tsx` at main d5f9ef31. The adapter case remains in the legacy suite and owns branding/transport integration. The package owns canonicalization algorithms.

M02 cases: SAN/UCI/promotion and FEN identity; null/illegal/invalid stop conditions; installed private/unavailable export rejection. See `tests/chess-line.test.mjs` for exact titles.
