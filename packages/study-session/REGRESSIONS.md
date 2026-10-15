# Study session regression ownership

`npm test` discovers `tests/study-session.test.mjs`. Named #219 cases cover pending
versus committed settlement, frozen lost-response retry and late settlement after
attempt replacement. Named #186 cases preserve bounded committed-review/next-load
failure and retry without another submission, including acquisition timeout.

These tests prove client coordination. They do not prove exactly-once PostgreSQL
effects, independent Learning deployment or service-worker behavior.
