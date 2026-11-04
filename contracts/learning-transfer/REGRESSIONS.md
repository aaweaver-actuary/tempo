# Retained-history contract regressions — #908 W01

Owning suite: `tests/test_retained_history_contracts.py` (21 original pure
regressions, unchanged assertions) and `tests/test_installed_contract.py`.

The original suite covers byte-exact FSRS/large IDs, immutable source identity,
full canonical PK and codecs, collision/references, inventory omission,
required archive/translation rejection, replay/conflicts, final fencing,
representative activation denial, generation binding and forward recovery.

Installed-artifact tests check distribution identity/resources, packaged schema
parity, public imports without runtime/database/provider infrastructure, and
unchanged golden envelope digests. `examples/producer.py` and `consumer.py`
exchange representative and explicitly blocked synthetic manifests.
This is pure component proof; no native transaction or activation claim.
