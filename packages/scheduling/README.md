# Pure scheduling

`tempo-scheduling==0.2.0` exports `Schedule`, `schedule_review`, and `unlock_ready`
from `tempo_scheduling`. FSRS stays pinned to 6.3.2; its parameters, rounding,
growth caps, reinforcement, tracks, and result fields are unchanged.

`schedule_review(outcome, *, reviewed_at, review_day, fsrs_card_json, ...)` requires
an aware review instant, a separate calendar date, and complete serialized FSRS
state. UTC-normalized instants drive FSRS; the supplied day drives due dates.
Every FSRS Card field must be present, including an explicit non-null `card_id`
and aware `due`. Initial `last_review`, stability, and difficulty may be null.
The supported FSRS decoder passes explicit identity/due values to its constructor,
bypassing clock-based IDs and collision sleeps. Missing inputs never create state.

The backend adapter retains the legacy signature, naive-time handling, UTC-now
and local-day defaults, and initial FSRS identity generation. These environmental
operations belong to that outer adapter. `Schedule` retains class identity;
`schedule_review` is now a forwarding wrapper.

Run `uv sync --locked --no-editable`, `uv run --locked --no-editable python -m build`,
and `uv run --locked --no-editable python -m pytest tests` here.

`tests/baseline.json` captures 12 explicit-state scenarios from main
`6b99085f8978346a959b5d4a6ea44b6e56f05dd6` before changes. Existing `golden.json`
remains unchanged. The baseline rejected non-UTC offsets; 0.2.0 accepts aware
offsets by preserving their instant in UTC and leaving `review_day` independent.
Fresh installed-process tests prohibit environmental APIs without replacing FSRS
constructors, decoding, or scheduling mathematics.
