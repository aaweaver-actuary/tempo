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
The offline worker still requires every emitted JS/CSS asset; its inventory is
not weakened to exclude optional chunks after they have been emitted.

[Canonical source composition](../../app/README.md#main-study-path-and-source-gap) and
[offline boundary](../../static/README.md#conditional-study-build-and-offline-assets-main)
records the branch implementation and evidence scope. Merged host/endgame repairs have separate
[Learning ownership](../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main);
deployment resource isolation remains unqualified by this frontend work.
