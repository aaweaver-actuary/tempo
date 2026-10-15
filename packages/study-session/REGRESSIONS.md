# Study session regression ownership

`npm test` discovers `tests/study-session.test.mjs`. Named #219 cases cover pending
versus committed settlement, frozen lost-response retry and late settlement after
attempt replacement. Named #186 cases preserve bounded committed-review/next-load
failure and retry without another submission, including acquisition timeout.

These tests prove client coordination. They do not prove exactly-once PostgreSQL
effects, independent Learning deployment or service-worker behavior.

PR #285: `pr285_rejected_answer_terminal_settlement_is_retained_and_never_redelivered`
proves terminal rejection, reload-safe repeat checks and blocked next acquisition.
`pr285_rejected_answer_late_delivery_returns_actual_durable_terminal_result`
proves that a delayed rejection cannot overwrite or misreport a committed winner.
