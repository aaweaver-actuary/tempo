"""Games-only HTTP composition: an immutable reader, no mutation/worker routes."""
from fastapi import APIRouter, Query
from .games_reader import GamesReader


def register(app):
    router=APIRouter(prefix='/v1/analysis')
    def reader():return GamesReader(app.state.store)

    @router.get('/bootstrap')
    def bootstrap():
        return {'schema_version':1,'workspace_id':app.state.store.workspace_id,
            'capabilities':['analysis-v1','selected-games-v1','games-readonly-v1'],
            'policy_version':'c-analysis-v1'}

    @router.get('/game-scopes')
    def scopes():return reader().scopes()

    @router.get('/game-scopes/{run_id}')
    def scope(run_id:str):return reader().scope(run_id)

    @router.get('/runs/{run_id}/games')
    def games(run_id:str,after:str|None=None,limit:int=Query(10,ge=1,le=10)):
        return reader().games(run_id,after,limit)

    app.include_router(router)
