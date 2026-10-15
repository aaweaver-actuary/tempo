# Learning review application regression ownership

The regular owning package command is `python -m pytest tests` against the installed
artifact. All parametrized identities in `tests/test_application.py` and
`tests/test_boundary.py` must collect and pass, including original receipt replay,
legacy broader projection replay, changed request binding, recorded clocks, guided
failure, recovered chronology, explicit registration, evidence and continuation.
`qualify.py` runs the same suite in source-free Python 3.12/3.14 consumers with
service guards. A failed, skipped or zero-selected suite is not qualification.

The separate owning native command is `python integration/run_postgres.py DIR` in
a legacy test environment containing installed tempo-learning-review and
 tempo-scheduling. It discovers `integration/test_postgres.py` in its own fresh
PostgreSQL cluster and exports every executed pytest identity to JUnit. It covers
ordinary evidence/history/schedule/receipt/queue effects, response loss and logical
retry, native competing connections, stage failures, handled savepoint conflict,
stale/deleted/archived distinctions, optional successor failures/lock contention,
connection termination with rollback and fresh-process replay, and active/inactive
frozen reader constraints. It never accepts a pre-existing server/database URL. The unmodified D component
selector also executes these same 43 pure and 24 native identities in the
disposable combined context; its externally supplied JUnit receipt is preserved.

Existing backend characterization remains in its regular suite: scheduling review
application/store, prepared review continuation, queue attempt recovery/issuance,
phone offline training and relevant study durability. The existing native training
transition regression injects the relocated adapter issuance seam without weakening
its deletion/revision contention assertions. Scheduler package/adapter/boundary
tests remain with C and are not edited here.
