# Learning runtime database tooling

[run_postgres.py](run_postgres.py) is the existing invocation-owned PostgreSQL native runner. From a full repository checkout, use the Learning runtime environment and optionally supply an evidence directory. It retains bounded disposable resources, exact collection/execution/JUnit checks, role provisioning and verified cleanup. The ordinary [runtime qualification workflow](../../../services/learning/runtime/qualify.py) remains application-owned; native proof selection remains [the runtime tests](../../../services/learning/runtime/tests).

[restore_rehearsal.py](restore_rehearsal.py) retains the existing explicitly configured disposable rehearsal restore and copied-review identity repair. Docker ownership/disposable labels, endpoint, workspace, empty-target and unchanged-history guards are unchanged. Its SQL and behavior are byte-identical; relocation grants no permission to run a restore or alter a database.

These tools are included in source distributions only. Runtime wheels exclude `tooling/` and `tests/`; offline before/after package comparison verifies the runtime payload. Native and restore behavior was not executed for this relocation. [Source schema documentation](../README.md) describes the stored tables; live deployment remains unverified.
