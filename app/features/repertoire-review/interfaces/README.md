# Repertoire Review public surface

Owner: #490's allocated UI batch. Repertoire alone approves/publishes content;
#501 owns injected concrete APIs, root mounting and caller migration.

`index.ts` names the existing client, read/position/finding/conflict, navigation,
request/command and view contracts. `loadRepertoireReviewView()` returns the
retained component on explicit request, without mounting it. Cold imports load
neither its private panels/session nor transport, engines or storage. The surface
exports no session constructor, proposal state, panel or content mutation writer.

The private UI files are byte-identical between the manifest's D/E checkpoint
`c5c5f2f0` and combined Games checkpoint `cbc22121`. This branch consumes the latter
to avoid resolving unowned shared-contract conflicts. No C/D/E behavior is added
or activated by this interface pass.

## Exact transition edges

- Client/read aliases still resolve through `../api.ts`, whose canonical evidence,
  proposal and receipt values come from `app/features/product-api/contracts.ts`
  (#482). No DTO is copied; migrate only after that owner supplies its TS entry.
- `RepertoireReviewViewProps.session` still references the existing private
  `RepertoireReviewSession` type. That optional compatibility edge is retained to
  preserve the exact signature; the constructor/state are not public exports.
  Extract an agreed semantic session capability only in a released view/state
  window, not by silently widening this new barrel.
- The retained view/panels still use private state, shared UI and board adapters.
  `app/features/product-api/RepertoireAuditMount.tsx` currently lazy-imports the
  private view and constructs `RepertoireReviewSession` from its private state
  module. #501 can migrate the view/intent imports to this surface, but the root's
  injected session remains an explicit private edge. Replacing that edge needs
  an agreed owner capability and a released view/state window that preserves its
  retry identity across audit refreshes. Neither the root nor state is edited here.

Remove old paths only after owner-approved caller adoption, semantic checks and
#482's private-import/cycle/export-bypass negative controls. Preserve approval,
pending receipts, retry keys and source-revision validation throughout.

Focused proof: `tests/unit/public-surface-repertoire-board.test.ts` checks cold
imports, named export limits, retained component identity and exact contract types.
Existing `product-repertoire-review.test.tsx` remains the owning UI semantic suite.
