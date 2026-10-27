# Persisted chess identity interface

Restructuring owner: #497 (allocation revision 2). Existing functional ownership
and algorithms are unchanged. Explicit named exports preserve object identities.

Operations: canonical_fen_key; card_id.

Dependencies: The same existing _identity.py functions; standard hashlib only. Persisted first-four-FEN-field/card hashing is unchanged, and is not legal-EP position equivalence.

Effects/transactions: none on import. This surface grants no learning/content
mutation authority; caller-owned capabilities retain their existing lifetime.

```mermaid
flowchart LR
  Caller["Declared consumer"] --> Public["Owner interfaces"]
  Public --> Existing["Existing owner implementation / pure values"]
```

Implemented source seam only; consumer adoption and deployed independence are
not established by the directory. Existing tempo_chess_core root imports remain compatibility edges. #501 routes callers and packaging; remove aliases only after #482 private-import/export-bypass negatives pass.

Focused proof: `backend/tests/test_public_surface_chess.py` covers identity, cold import and the changed neutral
semantic boundary. #501 owns central registration/root routing and #482 owns
whole-declared-graph cycle/private-import/export-bypass negative controls.
