"""Explicit manual work and reads; no source service installation is required."""
import json
from pathlib import Path
from .models import Manifest, Page, RunRequest, Command
from .store import AnalysisStore


def parser(commands):
    analysis=commands.add_parser("analysis")
    operations=analysis.add_subparsers(dest="analysis_command",required=True)
    accept=operations.add_parser("accept-input")
    accept.add_argument("--manifest",type=Path,required=True)
    accept.add_argument("--pages-directory",type=Path,required=True)
    run=operations.add_parser("run");run.add_argument("--file",type=Path,required=True)
    for name in ("status","report","remaining","work","continue","retry","cancel","resume"):
        operation=operations.add_parser(name);operation.add_argument("run_id")
        if name in ("continue","retry","cancel","resume"):
            operation.add_argument("--file",type=Path,required=True)
        if name in ("report","remaining"):
            operation.add_argument("--after");operation.add_argument("--limit",type=int,default=50)
        if name=="report":
            operation.add_argument("--generation",type=int)
            operation.add_argument("--category",choices=["repertoire_miss","practical_gap","engine_opportunity"])
            operation.add_argument("--dismissal",choices=["open","dismissed"])
        if name=="work":
            operation.add_argument("--max-jobs",type=int,default=1)
            operation.add_argument("--worker-profile",type=Path)
    for name in ("dismiss","reopen","export"):
        operation=operations.add_parser(name);operation.add_argument("run_id");operation.add_argument("finding_id");operation.add_argument("--generation",type=int,required=True)
        if name!="export":
            operation.add_argument("--file",type=Path,required=True)
        else:
            operation.add_argument("--kind",choices=["context","response-proposal"],default="context")
    inspect=operations.add_parser("input");inspect.add_argument("input_id");inspect.add_argument("--revision",type=int,required=True)
    operations.add_parser("inputs");operations.add_parser("runs")
    receipt=operations.add_parser("receipt");receipt.add_argument("command_id")


def execute(options,store,read_document):
    owner=AnalysisStore(store)
    operation=options.analysis_command
    if operation=="accept-input":
        manifest=Manifest.model_validate_json(read_document(options.manifest))
        owner.manifest(manifest)
        directory=options.pages_directory.resolve()
        for reference in manifest.pages:
            path=(directory/(reference.page_id+".json")).resolve()
            if path.parent!=directory:
                raise ValueError("invalid_page_filename")
            owner.page(Page.model_validate_json(read_document(path)))
        return owner.finalize(manifest.input_id,Command(command_id="finalize:"+manifest.input_id+":"+str(manifest.revision),action="finalize",expected_revision=manifest.revision))
    if operation=="input":
        return owner.input_status(options.input_id,options.revision)
    if operation=="run":
        return owner.submit(RunRequest.model_validate_json(read_document(options.file)))
    if operation in {"continue","retry","cancel","resume"}:
        command=Command.model_validate_json(read_document(options.file))
        if command.action!=operation:
            raise ValueError("command_action_mismatch")
        return owner.command(options.run_id,command)
    if operation in {"dismiss","reopen"}:
        command=Command.model_validate_json(read_document(options.file))
        if command.action!=operation:
            raise ValueError("command_action_mismatch")
        return owner.dismiss(options.run_id,options.finding_id,options.generation,command)
    if operation=="export":
        return owner.export(options.run_id,options.finding_id,options.generation,options.kind)
    if operation=="report":
        return owner.report(options.run_id,after=options.after,limit=options.limit,category=options.category,dismissal=options.dismissal,generation=options.generation)
    if operation=="remaining":
        return owner.remaining(options.run_id,options.after,options.limit)
    if operation=="work":
        from .worker import work
        return work(owner,options.run_id,options.max_jobs,options.worker_profile)
    if operation=="receipt":
        return owner.receipt(options.command_id)
    if operation in {"runs","inputs"}:
        return getattr(owner,operation)()
    return owner.status(options.run_id)
