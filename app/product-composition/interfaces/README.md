# Frontend composition entry

Owner: #501. `createProductGameReviewApi` and `gameReviewEndpoints` bind the
published Game Review public factory to the existing product transport paths.
Construction performs no request or storage write. Consumers supply navigation
and ordinary Study intent handling; this surface does not mount a new view.

The owning feature remains `app/features/game-review/interfaces`. Its original
history/receipt implementation was adopted from published cbc22121 without
merging the unrelated proof-context branch. Repertoire mounting now uses the
public view loader and navigation type; its private session constructor remains
an explicitly assigned #490 follow-on, not completed decoupling.
