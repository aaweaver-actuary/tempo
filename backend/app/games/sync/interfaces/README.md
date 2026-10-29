# Game sync interface

Owner: #491, `game-sync` in allocation revision 2.

This first surface exposes the existing typed request, account/filter, job and
provider result/status contracts from `app.contracts.transport.game_sync` with
their original identity. It adds no DTO, defaults, receipt semantics or effects.
That canonical transport origin is explicit during the mechanical transition;
#482/#501 own its shared-contract/caller registration, not a copied definition.
Imports perform no provider, connection, worker, queue or application startup.

Execution is deliberately not exported through this surface. The existing
`services/game_sync_coordinator.py` imports private Study/repertoire workflows;
re-exporting its coordinator would hide those edges. The newer
`product_composition/game_sync.py` is a root implementation, never an inward
domain dependency. #501 alone selects concrete worker implementations and public
owner capabilities. The existing application remains unchanged; no placeholder
is presented as an operational sync capability.

`backend/tests/test_public_surface_game_sync.py` proves exact value identity,
request/job validation, explicit exports and cold import with concrete worker/
Study/provider/root modules blocked. The default continuous CLI remains blocked
pending the separately qualified receiving/refresh and packaged-profile boundary.
