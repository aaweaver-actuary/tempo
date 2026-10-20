# Game classification boundary (scaffold only)

`tempo_game_classification.UnimplementedClassification.classify` returns an explicit
`not_implemented` value. It does not classify games, contact another component,
open storage, enqueue work or mutate Study/Content. No action can succeed through
this stub. It depends only on the shared [game review values](../game-review-contracts/README.md).

This is an injectable placeholder, not a server and not an activated feature.
The implemented ingestion and comparison components do not call or import it.
Future implementation must retain separate ownership and qualification.

Owning check: `uv sync --locked --no-editable && uv run --locked --no-editable python -m pytest tests -q`.
