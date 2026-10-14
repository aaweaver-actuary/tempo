# Scheduling package

Public entry: `tempo_scheduling`; exports `Schedule`, `schedule_review`, `unlock_ready`. Existing FSRS 6.3.2 behavior is unchanged. Run `uv sync --locked --no-editable`, `uv run --locked --no-editable python -m build`, and `uv run --locked --no-editable python -m pytest tests`.

The existing optional clock defaults are a compatibility exception owned by #200. FSRS also generates a clock-based card ID when no serialized card is supplied. Deterministic consumers/tests supply `reviewed_at`, `review_day`, and serialized initial cards with explicit IDs. #200 owns removing these compatibility exceptions. This package owns no transaction, database, queue writer or service contract.
