# Study practice UI public surface

Owner: #490 UI. #487 retains Study learning/runtime authority; #501 owns root
composition and caller migration. This new surface does not edit or activate
those owners' working files during the critical Study repair.

`TrainingViewProps` and `StudyPracticeComponent` alias the exact existing private
`app/views/training_view.tsx` signature. `loadTrainingView()` explicitly loads and
returns that same component; it neither wraps nor mounts it. Cold imports expose
contracts/entry points without loading the controller. Props, defaults, grading,
pending-save recovery and queue callbacks remain unchanged.

`app/views/home_view.tsx` is the retained Study composition/storage shell. It is
not re-exported as a feature implementation. There is no new public Home runtime
entry, independent controller or substitute Study scheduler in this surface.

## Exact transition edges

The prop alias still resolves types through `app/views/training_view.tsx`, including
`PracticeCard`, `PendingReview`, board types and assistance callbacks. The retained
view still imports `app/state/training-store`, `app/lib/study-connection`, board
publishing hooks, endgame/defense/exercise views and existing save-notice helpers.
These are unresolved private/legacy edges; a lazy public entry is not their
decoupling proof. No store, view state or sibling implementation is exported here.

#501 owns the current `app/views/home_view.tsx` import of `./training_view` and
must preserve its Suspense/mount timing and props when adopting this lazy entry.
The same shell imports Tactics directly; neither caller is edited here.
Moving the original props declaration
into this directory and making the view import inward requires an exact released
`app/views/training_view.tsx` lease, then semantic and #482 import/DAG checks.
The first-wave allocation grants no such edit or root window; old paths remain.

Effects remain in the existing Study/storage owners. Loading/mounting the retained
view after composition explicitly requests it still has its legacy dependencies;
this pass claims only the supported signature, cold entry and unchanged identity.
`tests/unit/public-surface-study-games.test.ts` is the focused surface check.
