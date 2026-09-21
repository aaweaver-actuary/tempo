# Backend application

`backend/app` is the Python application package for the local FastAPI product.

`main.py` is the current composition root and HTTP surface. `database.py`
provides the public connection seam and SQLite initialization. `models.py`
holds transport models. Business logic lives under `services/` and should not
be added to route handlers when it can be expressed as a testable function.

The package is intentionally SQLite-first. Keep storage failures visible and
preserve the foreground/background connection distinction when adding derived
work.
