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

Position-owned cohorts use the additive public `clone_state` and
`schedule_position_review` APIs, with `PositionSchedule` and `PositionGrade`.
`initial_position_state(allocated_card_id, due_at)` returns `InitialPositionState`
with the fresh serialized FSRS state and due instant. The ID must be an externally
allocated positive integer and the due instant must be aware. The result is
Learning step zero with no review history, stability or difficulty. Study supplies
this pure operation to storage after admission; storage retains allocation,
uniqueness, fences and insertion in the existing borrowed transaction. It creates
no review and does not read a clock or persist anything.

`clone_state(fsrs_json, *, allocated_new_card_id)` validates coherent complete
FSRS 6.3.2 Card JSON through the supported decoder and roundtrip, preserving
memory/due/state/step while rekeying. A fresh unreviewed Learning card is valid;
partial memory, unknown fields/version envelopes, invalid numbers and naive dates
hold with `ValueError`. Optional legacy physical presentation metadata is dropped.
The allocator must provide a positive ID different from the donor, and verify
uniqueness across persisted position and legacy FSRS IDs; a pure function cannot
prove database uniqueness. Original donor history/provenance stays with its owner.

`schedule_position_review(grade, *, fsrs_card_json, accepted_at, review_day)`
accepts only `good` or `again`, an aware acceptance instant, and the explicit local
Study date. It calls actual pinned FSRS once at acceptance in UTC, using the
existing retention .92, 365-day cap, no fuzzing or learning/relearning steps.
`PositionSchedule.due_at` and serialized `due` retain the actual FSRS instant;
`due_date` is the supplied local date plus `interval_days`. Physical first-correct,
reinforcement and hard/light metadata reset; `requeue_today` is always false.
This adapter does not use the legacy wrapper's first-clean reinforcement, track,
lateness or growth overrides. Existing `schedule_review` behavior is unchanged.

The Study transaction writer must authorize each grade, enforce same issued and
accepted local day for Good, due-at-issuance, revision/epoch/definition fences,
receipt idempotency and at most one positive per position/day. A distinct later
wrong may still receive Again at acceptance. The pure adapter has no daily ledger
and never claims persistence, concurrency, eligibility or whole-attempt proof.
