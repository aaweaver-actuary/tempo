# Static entry point

`main.tsx` mounts the shared Tempo application for the `/tempo/` base path,
loads shared styles, installs the error boundary, and registers the service
worker in production. Keep runtime-specific bootstrapping here and product
behavior in `app`.

## Independent Study target disposition

`TEMPO_TARGET=study` in `vite.static.config.ts` substitutes
`study-main.tsx` via `scripts/study-composition.ts`; that entry lazily mounts
`StudyConnectionBoundary`. `npm run build:study` and the
`study-composition-regressions` / `study-build-closure-regressions` tests still
select and inventory that separate source closure. This establishes build
reachability, not a supported serving backend or an installed capability.

The current SPEC retires the independent Learning service and database. The
supported product direction is the existing monolith entry (`main.tsx` and
`legacy-home`) with ordinary Study, Tactics, frozen exercises, offline and
reload recovery. The independent entry is a dormant recovery-only hold/export
surface or may be removed once retained recovery remains reachable. It must not
be used to justify restoring the retired service. No source behavior changes in
this decision.

FE36 owns eventual removal of the `study-main.tsx` target substitution,
`TEMPO_TARGET=study` / `__TEMPO_STUDY__` serving controls and connection UI
only after FE31/FE32 and the delivery owner establish the supported-shell
command and retained-recovery paths. Keep authority inspection/export and
original-service recovery capabilities reachable; do not delete their journals
or turn an unconfirmed authority into the current monolith authority. See the
[Study composition disposition](../../app/features/study/README.md#independent-serving-and-retained-work-disposition)
for the consumer map and handoff.
