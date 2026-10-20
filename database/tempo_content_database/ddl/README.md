# Content ddl

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [migrate.py](migrate.py) | Explicit Content-owned migration authority and runtime compatibility checks. |

[Ordered SQL migrations](migrations/README.md). Moving these files does not apply them. Migration bytes/checksums and order are preserved; the legacy wrapper’s installed-resource path changes only.
