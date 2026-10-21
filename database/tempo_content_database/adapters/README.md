# Content adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [store.py](store.py) | Content-only PostgreSQL authority; computation happens between short sections. |
| [bridge.py](bridge.py) | Existing bridge SQL, JSON driver encoding, source-scope locks and row projections; mutations borrow the original transaction. [Application/composition](../../../services/content/src/tempo_content/bridge/README.md). |
