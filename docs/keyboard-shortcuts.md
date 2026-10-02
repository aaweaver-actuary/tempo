# Contextual keyboard shortcuts

Implementation validation plan: navigation must stay within revealed positions;
historical browsing must not change live attempts, grading, drafts or scheduling.
One active visible board receives each command. Popup dismissal must close one
layer and restore focus. Text entry, widgets, modifiers and IME retain their keys.

Start with named failing Escape regressions for local data, notifications and
Builder search, then focused dispatcher/exercise regressions and existing board,
editor and dialog callers. Use real PostgreSQL-backed browser cases for piece
placement, ownership, input cancellation and cross-browser focus; use pinned
visual checks for help and Settings. CI owns required final candidate validation.
No database schema, backend business rules or background handlers change.

## Behavior

| Key | Command |
| --- | --- |
| Left / Right | Previous / next revealed ply |
| Up / Home | Actual line start (including a custom FEN) |
| Down / End | Furthest revealed position |
| F | Flip the active board |
| R | Restore orientation and the live decision or working position |
| H | Existing enabled Hint action, including its grading consequences |
| N | Existing enabled Next or Continue action |
| ? | Contextual keyboard help |
| Escape | Dismiss one topmost popup; otherwise the newest visible toast |

Historical exercise positions are read-only. Browsing never changes the live
attempt or requests engine/tablebase analysis. If the submitted/live FEN is
outside the revealed reference line, it remains separate: Home/End browse the
reference, Previous enters at its revealed frontier, and R returns to that live
answer. Continue to defense and N both leave refutation browsing and restore the
original legal defensive decision without changing recognition or hint results.
A history boundary consumes navigation without moving further. Static previews
retain browser Arrow/Home/End behavior while supporting F/R/help. R also
cancels held input and fences deferred callbacks at the same position.

The dispatcher targets the last board clicked or focused, defaults to the
selected/source board, and gives popup boards priority. Typing, text selection,
modifiers, IME and handled widget events retain their behavior. Only navigation
repeats when a key is held. Settings → Board → Letter keyboard shortcuts disables
F/R/H/N immediately and stores the preference in this browser; other keys remain
available. Shortcut buttons are attached to every board, including previews.

The shared board publisher preserves callback identity while exposing current
availability. Public services, database schemas and grading APIs are unchanged.
`historyKeyboardActions` explicitly enables `capturesNavigation`; static boards
leave that capability disabled, independently of command availability.
Popup registration shares one Escape dispatcher with focus restoration.

## Validation scope and ownership

Risk is concentrated in board ownership, browser event dispatch and concealed
exercise continuations. Regular unit regressions exercise state boundaries and
held/delayed input; real browser cases verify actual rendered pieces and focus.
Shared-board, editor, capture, discovery, notification and dialog callers are
included. The new browser spec is registered in the complete board CI family.
Pinned screenshots cover help and the Board setting on phone and desktop widths.

The three named Escape regressions failed on the initial main baseline before
the fix (3 failures, 5.84s) and subsequently passed (3 tests, 2.92s). No live study
services or database were used by tests. Browser runs create and remove only
uniquely named disposable PostgreSQL resources. Dependencies were installed once
in this clone; the selected unit scopes are Node-only.

CI owns final required candidate validation, including all core/integration,
durability, source-selected browser families, pinned checks and the current-base
merge result. Focused local passes do not establish merge or release readiness.
No local full gate or deployment is requested. See the
[validation record](keyboard-shortcuts-validation.md) for exact commands,
durations and tested revisions.
