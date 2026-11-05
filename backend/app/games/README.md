# Games records and ingestion

Games owns the provider adapters, PGN normalization, source records, and the
repertoire comparison and feedback workflow. Provider acquisition produces
`GameRecord` values with the original PGN and provider identity. The current
product workflow normalizes those bytes through `PgnGameIngester`, then commits
acquired pages and checkpoints through the Games store. Game Review reads that
store; Study owns learning effects and scheduling.

Lichess and Chess.com adapters retain their configured account, filters, and
bounded request behavior. Reading or replaying a game does not acquire games or
deliver feedback. See [feedback](feedback/README.md) and the provider guides for
the retained workflows.
