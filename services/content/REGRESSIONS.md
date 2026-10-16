# Content regression ownership

Content's regular installed `tests/` suite owns strict authored-input validation,
legal-state canonicalization, both colors, castling/en-passant, promotions,
terminal positions, complete reply identities, shared/transposed position reuse,
policy separation, stable card/decision identity, permutation determinism and
immutable publication contract behavior.

`tests/test_packaging.py` owns the named independent-distribution, packaged
migration-manifest, and import/CLI-without-service-initialization regressions.
These same tests execute against source-free installed wheel and sdist consumers;
they do not rely on a source checkout or ambient `PYTHONPATH`.

`integration/test_postgres.py`, executed only by the owned disposable native
runner, owns real migration/grants, persistence/restart, interrupted bounded
reconciliation, semantic replay, stale leases/generations, concurrent publication,
structural edits, shared membership safety, orphan cleanup/restart, failed
publication preservation, validation rollback and foreign database denial.
Deletion must retain a durable collector, and deletion/reconciliation must obey
one lock order. These substantive donor #117 invariants remain native regressions.

`integration/test_consumers.py` and its standalone `consumer_probe.py` own actual
installed Learning/Game Evidence adapter compatibility, complete executable kinds,
unchanged publication identities, source/learner-color provenance, null activation,
and the explicit rejection of a 129-fact current Learning bundle. Consumer
implementations remain test-only inputs and are absent from Content's production
dependency closure.

`run_suite.py` records independent collection and execution inventories and
requires identical nonzero unique test identities, passed setup/call/teardown,
and matching successful JUnit cases. The artifact qualifier applies the same
guard to Python 3.12/3.14 wheel/sdist consumers and proves pytest's zero-selection
exit separately. No selected skip, deselection or absent native proof is a pass.

The Content entry in `components.json` is the regular component discovery owner;
this local inventory replaces a duplicate root regression list for Content.
Source-bound results and any remaining gaps are recorded in `VALIDATION.md`.
