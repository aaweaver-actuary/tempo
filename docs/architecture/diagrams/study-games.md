# Study and minimal Game Review

[Current ownership](system.md) · [Transaction flow](flows.md) · [Reduction audit](../reduction-pass2/README.md)

```mermaid
flowchart TD
  Home[Home] --> Controller[Public Study controller: snapshot and actions]
  Controller --> Session[Existing session, outbox and journals]
  Session --> Transport[Review transport]
  Transport --> Writer[Foreground writer]
  Writer --> Receipt[Atomic review, effects and receipt]
  Receipt -->|confirmed result| Acquire[Queue acquisition]
  Acquire --> Routes[Three queue GET routes]
  Routes --> Compose[Study composition]
  Compose --> Next[Study next exercise operation]
  Next -->|injected read and issue capabilities| Reads[Borrowed tempo_storage reads]
  Next --> Issue[Issue or replay original position plan]
  Issue --> Manifest[Position checkpoint manifest]
  Manifest --> Queue[Flat queue or truthful hold]
  PGN[Pasted PGN or saved game] --> Replay[Full legal replay]
  Replay --> Facts[Read current authored facts]
  Facts --> Compare[Typed first departure comparison]
  Compare --> Result[Departure, coverage end, covered or unavailable]
```

The Study owner rechecks the current day and head after issuance. Original
attempts, first-response checkpoints and human review receipts retain their
identity. Browser and native tests are needed to establish the assembled
saved-review to playable-next journey; this source map is not that evidence.

Game Review reads storage without provider acquisition or Study effects. Retired
automatic feedback and recombination do not become current through preserved
historical records. Pasted PGN comparison does not save a game.
