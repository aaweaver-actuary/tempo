# Intelligence ddl

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [migrate.py](migrate.py) | Explicit Intelligence owner operation. Startup never calls migrate. |

[Ordered SQL migrations](migrations/README.md). Moving these files does not apply them. Migration bytes/checksums and order are preserved; the legacy wrapper’s installed-resource path changes only.
