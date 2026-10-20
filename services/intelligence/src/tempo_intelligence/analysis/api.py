"""Narrow authenticated report consumer surface, registered by the existing owner."""
from fastapi import APIRouter, Query
from .models import Manifest, Page, RunRequest, Command
from .store import AnalysisStore


def register(app):
    router=APIRouter(prefix="/v1/analysis")
    def owner():
        return AnalysisStore(app.state.store)

    @router.get("/bootstrap")
    def bootstrap():
        return {"schema_version":1,"workspace_id":app.state.store.workspace_id,"capabilities":["analysis-v1","informational-export","selected-games-v1", *(["games-readonly-v1"] if app.state.read_only else [])],"policy_version":"c-analysis-v1"}

    @router.get('/game-scopes')
    def game_scopes():
        from .games_reader import GamesReader
        return GamesReader(app.state.store).scopes()

    @router.get('/game-scopes/{run_id}')
    def game_scope(run_id:str):
        from .games_reader import GamesReader
        return GamesReader(app.state.store).scope(run_id)

    @router.get("/inputs")
    def inputs():
        return owner().inputs()

    @router.post("/inputs")
    def manifest(payload:Manifest):
        return owner().manifest(payload)

    @router.post("/inputs/pages")
    def page(payload:Page):
        return owner().page(payload)

    @router.get("/inputs/{input_id}")
    def input_status(input_id:str,revision:int=Query(ge=1,le=2**53-1)):
        return owner().input_status(input_id,revision)

    @router.post("/inputs/{input_id}/finalize")
    def finalize(input_id:str,payload:Command):
        return owner().finalize(input_id,payload)

    @router.get("/runs")
    def runs():
        return owner().runs()

    @router.post("/runs")
    def run(payload:RunRequest):
        return owner().submit(payload)

    @router.get("/runs/{run_id}")
    def status(run_id:str):
        return owner().status(run_id)

    @router.get("/runs/{run_id}/report")
    def report(run_id:str,after:str|None=None,limit:int=Query(50,ge=1,le=100),category:str|None=None,dismissal:str|None=None,generation:int|None=Query(None,ge=1)):
        return owner().report(run_id,after=after,limit=limit,category=category,dismissal=dismissal,generation=generation)

    @router.get("/runs/{run_id}/games")
    def games(run_id:str,after:str|None=None,limit:int=Query(10,ge=1,le=10)):
        return owner().games(run_id,after,limit)

    @router.get("/runs/{run_id}/remaining")
    def remaining(run_id:str,after:str|None=None,limit:int=Query(64,ge=1,le=100)):
        return owner().remaining(run_id,after,limit)

    @router.post("/runs/{run_id}/commands")
    def command(run_id:str,payload:Command):
        return owner().command(run_id,payload)

    @router.get("/results/{result_id}")
    def result(result_id:str):
        return owner().engine_result(result_id)

    @router.get("/commands/{command_id}")
    def receipt(command_id:str):
        return owner().receipt(command_id)

    @router.get("/findings/{finding_id}")
    def finding(finding_id:str,run_id:str,generation:int=Query(ge=1)):
        return owner().finding(run_id,finding_id,generation)

    @router.post("/findings/{finding_id}/commands")
    def dismiss(finding_id:str,payload:Command,run_id:str,generation:int=Query(ge=1)):
        return owner().dismiss(run_id,finding_id,generation,payload)

    @router.get("/findings/{finding_id}/export")
    def export(finding_id:str,run_id:str,generation:int=Query(ge=1),kind:str="context"):
        return owner().export(run_id,finding_id,generation,kind)

    app.include_router(router)
