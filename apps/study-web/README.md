# study-web

Owner: Tempo / #251. The independent package extraction remains planned (M02).
PR #324 implements a physical Study composition in the existing static toolchain;
it does not claim an independently installable package or deployment.

`TEMPO_TARGET=study` selects `static/src/study-main.tsx` through
`scripts/study-composition.ts`. It reuses Home's single training controller and
core views. The full local/demo entry composes optional views, dialogs and their
state through `legacy-home.tsx` / `legacy-workspace.tsx` and narrow typed slots.
Study retains Learning settings and browser appearance preferences. Unsupported
authoring actions are omitted when their capabilities are absent.

The build rejects named optional implementation roots and emits
`study-composition.json` for loaded Study modules, emitted chunks/assets,
shared dependency groups, and named optional-domain contract exceptions.
Its `assets` field is the bundler emission inventory, not an exhaustive list of
every file copied from `public/`.
The offline worker still requires every emitted JS/CSS asset; its inventory is
not weakened to exclude optional chunks after they have been emitted.

[Canonical source composition](../../app/README.md#main-study-path-and-source-gap) and
[offline boundary](../../static/README.md#conditional-study-build-and-offline-assets-main)
records the branch implementation and evidence scope. Host execution boundaries and full Endgames retirement have separate
[Learning ownership](../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main);
deployment resource isolation remains unqualified by this frontend work.

## Web image build

From the repository root, build the Study-only static image with
`docker build -f Dockerfile.study-web -t tempo-study-web:local .`. Set
`--build-arg TEMPO_WORKBENCH_URL=https://workbench.example` when the Study shell
should link to a Workbench origin; Learning's API origin is supplied by the
browser connection and proxied by nginx at runtime.
