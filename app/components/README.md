# Shared components

Reusable React components live here: the shared-board interaction adapter, persistent board
shell, controls, dialogs, navigation, feedback, and common buttons. Components
may use hooks and domain adapters, but screen-specific data fetching belongs in
the owning view or feature hook.

All production boards use [`@tempo/board-ui`](../../packages/board-ui/README.md#appearance-and-adopted-consumers)
and its public stylesheet/assets. [Chessboard](board/chessboard.tsx) adapts legal
moves, promotion, hints, sound, keyboard/help and viewport/frame allocation. It
constructs no renderer. [The persistent shell](board/persistent-board-shell.tsx)
keeps one Board mounted across workspace ownership changes; exact lease identity
and position revision fence obsolete input. Review screens receive appearance
from composition; they do not read another feature's store.

Shared native-element UI primitives are in `ui.tsx` with tokens and styles in
`ui.css`. The category inventory and workspace review checklist are in
`docs/UI-CONSISTENCY-AUDIT.md`.

The board shell has one important invariant: one Chessground instance owns the
board surface while workspaces and modal owners change around it. Preserve the
board-shell regressions when changing ownership or layout.

## Tactic capture

The [capture dialog](tactic-capture-dialog.tsx) owns draft editing, explicit
acquisition cancellation, submit exclusion and reset/focus after confirmation.
It reuses the existing editor/board and
[durable capture command](../lib/tactic-capture-command.ts). The
[Lichess adapter](../lib/tactic-capture-lichess.ts) acquires/validates only a
requested puzzle; no startup provider work or persistence authority is added.
The [existing Puzzle Rush parser](../lib/tactic-capture-pgn.ts) stays unchanged.

```mermaid
sequenceDiagram
    actor User
    participant Dialog as TacticCaptureDialog
    participant Input as PGN parser / Lichess adapter
    participant Command as saveTacticCapture
    participant Owner as Existing capture API/owner
    User->>Dialog: Paste input, Capture once
    Dialog->>Input: Parse PGN or explicit GET puzzle ID (10s limit)
    alt Input changed / dialog closed during fetch
        Dialog->>Input: Abort; ignore late result
    else Valid current input
        Input-->>Dialog: Solver FEN + all solution moves + provenance
        Dialog->>Command: Existing capture draft
        Command->>Command: Persist UUID + exact bytes before send
        Command->>Owner: Existing POST /api/tactics/captures
        Note over Command,Owner: Existing owner retains validation, deduplication, history, queue and atomic receipts
        alt Confirmed save / reuse
            Owner-->>Command: Authoritative receipt
            Command-->>Dialog: Confirmed result
            Dialog-->>User: Notify; clear/refocus; stay open
        else Failed / uncertain / pending
            Command-->>Dialog: Failure or unresolved command
            Dialog-->>User: Keep input/note/draft; retry existing identity if pending
        end
    else Invalid input / provider failure
        Input-->>Dialog: Error; no save
        Dialog-->>User: Keep input and authored draft
    end
```

Source-only checkpoint for #541; normal Chess.com export semantics and deployed
integration remain unverified. See [scope and fallback](../../docs/TACTIC-CAPTURE.md#browser-behavior).
No backend boundary, database writer/transaction, review history, queue policy,
or Study lifecycle changes. The explicit HTTP request shares the browser process;
deployment/resource isolation is not claimed from this source change.
