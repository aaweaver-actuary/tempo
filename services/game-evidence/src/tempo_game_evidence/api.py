"""Read API; JSON reports are also usable without this server."""
from fastapi import FastAPI, HTTPException, Query

from .store import EvidenceStore


def create_app(store: EvidenceStore):
    app = FastAPI(title="Tempo Game Evidence", version="0.1.0")

    @app.get("/healthz")
    def health():
        try:
            with store.connect() as database:
                database.execute("SELECT 1 FROM evidence_schema_migrations WHERE version=1")
            return {"status":"ready"}
        except Exception:
            raise HTTPException(503,"game_evidence_database_unavailable") from None

    @app.get("/v1/games")
    def games(workspace_id: str, after: str = "", limit: int = Query(64,ge=1,le=100)):
        return {"schema_version":1,"games":store.list_games(workspace_id,after,limit)}

    @app.get("/v1/games/{game_id}/comparison")
    def report(game_id: str, workspace_id: str):
        try:
            return store.report(workspace_id,game_id)
        except KeyError:
            raise HTTPException(404,"game_not_found") from None

    @app.get("/v1/imports/{import_id}")
    def import_status(import_id: str,workspace_id: str):
        try:
            return store.import_status(workspace_id,import_id)
        except KeyError:
            raise HTTPException(404,"import_not_found") from None

    return app
