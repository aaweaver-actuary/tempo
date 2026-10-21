# Content adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [store.py](store.py) | Content-only PostgreSQL authority; computation happens between short sections. Existing [source policy](../../../services/content/src/tempo_content/policy/README.md) and [worker orchestration](../../../services/content/src/tempo_content/worker/README.md) live in Content application folders; this adapter supplies their facts/operations. Route and publication orchestration remain extraction work. |
| [bridge.py](bridge.py) | Existing bridge SQL, JSON driver encoding, source-scope locks and row projections; mutations borrow the original transaction. [Application/composition](../../../services/content/src/tempo_content/bridge/README.md). |
