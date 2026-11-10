# Games storage

These adapters retain imported provider facts and the current product Game Review / repertoire-membership flow. They borrow the owning command or worker transaction.

| Storage module | Retained responsibility |
| --- | --- |
| [mutations.py](mutations.py) | Imported-game inclusion setting. |
| [synchronization.py](synchronization.py) and [sync](sync/README.md) | Provider-sync admission, jobs, deterministic provider windows and imported records. |
| [imports](imports/README.md) | Legacy raw provider import records and sync state. |
| [coordinator](coordinator/README.md) | Compatibility provider-sync and integrity recovery queries. |
| [authority](authority/README.md) | Immutable provider capture and source acceptance. |
| [product_sources.py](product_sources.py) | Canonical product game source facts. |
| [product_review.py](product_review.py) | Current Game Review projections. |
| [product_feedback.py](product_feedback.py) | Current product feedback/outbox transactions. |
| [managed_mode.py](managed_mode.py) and [collection_observations.py](collection_observations.py) | Current product mode and collection observations. |

The old seven-stage Games derivation chain, automatic global repertoire refresh sweep, engine analysis leases/publications, findings, gameplay events, feature statistics, tactical opportunities and their exclusive adapters are removed from source. Repertoire authoring no longer enqueues the old sweep. Current provider ingestion and product repertoire comparison remain in their existing owners.

This source cut does not drop tables, migrate records or delete learning history. Historical schemas and stored facts remain for existing accepted Study state and later explicit storage cleanup. The package still contains cross-domain storage responsibilities; this cut does not claim independent Games storage ownership or live deployment verification.

`product_feedback.has_determined_delivery_in_transaction` reuses the existing
pending-source predicate on the caller's borrowed transaction. It reads only
Games outbox facts, returns a boolean, and never changes transaction settings,
opens a connection, locks rows, replays sources or dispatches work. Composition
contains optional failures with a savepoint and checks the managed-mode hint;
actual acceptance still uses the separately locked mode/source/current fences.
No schema changes accompany this borrowed-read adapter.

`product_feedback.retained_page` reads one immutable original provider page by
job and input continuation for the [finite historical operator](../../../../backend/app/product_composition/README.md#one-time-historical-games-scripts).
Zero or multiple matching pages fail closed. Historical jobs use the additive
JSON purpose `history`; generic claims accept only `sync` (including older rows
with no purpose), while exact claims retain account leases and retry admission.
History therefore does not become a forward or Refresh-now checkpoint. Existing
tables, atomic page/capture/source/outbox transactions, keys and ownership are
unchanged; no new migration or storage relationship is introduced.

The canonical source adapter recognizes Lichess's legacy PGN UTC-second and a
matching provider millisecond timestamp only with exact PGN, provider identity,
player, moves, result and starting position. It retains the legacy row and raw
provider capture. Its explicit first-page history recovery locks and revalidates
the quarantined job, original page, initial source/capture, existing game and
empty outbox, then appends a resolution capture and updates that source. The
job can resume only when every source in that saved page is available and its
original serialized page key is verified. Other quarantine cases stay held.

For a historical Lichess response-limit failure, the Games owner can requeue
only the exact unchanged failed job when its saved configuration and prior
page are fenced and no page has committed at the failed input cursor. A typed
receipt of the original failure and cursor digest remains in the job payload.
This does not change any game source, capture, page or Study state.

## Historical daily-game repair

The Games owner maps Chess.com daily to classical, matching the acquisition
adapter while retaining original provider metadata. Its guarded historical
recovery keeps the original capture, adds resolution evidence, fences the
saved configuration and source identities, and authorizes replay of the exact
saved page only for the proven effect-free daily mapping conflict. A missing
canonical game is created in that same transaction.

The Review, Feedback and managed-mode stores reuse the transaction already opened
and bounded by the exact `DedicatedGamesConnections` owner. They do not nest a
second savepoint or reissue limits inside that same 250ms budget. Injected callback
connections retain their existing savepoint/limit wrapper. No deadline, mutation,
lock, receipt or outer commit ownership changes.
