# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis. Network/engine work never holds a transaction. |
| [store.py](store.py) | Intelligence owns every transaction here; no provider executes inside this boundary. |
