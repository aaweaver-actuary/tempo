# Tactics UI public surface

Owner: #490 UI; backend Tactics/Study effects remain #487-owned. #501 alone wires
root navigation and package/registry adoption. No active capture, solve, progress,
queue or Study-repair source is changed by these new files.

`TacticsViewProps` and `TacticsComponent` alias the existing private view's actual
signature. `loadTacticsView()` explicitly returns its same default component.
Importing the surface does not import/mount the controller, open storage, fetch a
catalog or start capture. Existing callbacks and default behavior are retained.

## Exact transition edges

The type alias resolves through `app/views/tactics_view.tsx`. That implementation
still imports `app/lib/study-command-client`, `app/lib/study-connection`, progress,
catalog/workspace readers, board publishing and capture-dialog implementations.
Those existing concrete UI/transport/storage dependencies have not been replaced
by injected capabilities. They are not hidden behind a claim of independence.

#501 should replace its existing lazy view import with
`lazy(async () => ({ default: await loadTacticsView() }))`, preserving mount timing.
The prop declaration can move inward only after an exact view-file lease and its
focused capture/solve/protected-state checks; #482 must then qualify the changed
graph and export-bypass controls. No compatibility alias is removed now.

The supported surface exports no progress store, catalog writer, capture owner or
Study mutation. Existing owners retain effects and resource lifecycle after the
root explicitly loads/mounts the view. Surface proof is in
`tests/unit/public-surface-study-games.test.ts`; it does not claim native Tactics,
whole-graph or installed-runtime acceptance.
