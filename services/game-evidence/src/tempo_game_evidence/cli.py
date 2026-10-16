"""Independent CLI. Every database/provider input is explicitly configured."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading

import uvicorn

from .api import create_app
from .delivery import deliver_one, learning_token
from .providers import AccountImport, enqueue_import, import_one, cancel_import
from .snapshot import ComparisonSnapshot
from .store import EvidenceStore
from .source import MAX_SOURCE_BYTES


def main(arguments=None):
    parser = argparse.ArgumentParser(prog="tempo-game-evidence")
    parser.add_argument("--database-url",default=os.environ.get("TEMPO_GAME_EVIDENCE_DATABASE_URL"))
    commands = parser.add_subparsers(dest="command",required=True)
    commands.add_parser("migrate")
    pgn = commands.add_parser("import-pgn")
    pgn.add_argument("file",type=Path)
    pgn.add_argument("--workspace",required=True)
    pgn.add_argument("--player",required=True)
    pgn.add_argument("--source-id",required=True)
    pgn.add_argument("--provider",default="pgn")
    account = commands.add_parser("import-account")
    account.add_argument("configuration",type=Path)
    cancel = commands.add_parser("cancel-import")
    cancel.add_argument("import_id")
    status=commands.add_parser("import-status")
    status.add_argument("import_id")
    status.add_argument("--workspace",required=True)
    snapshot = commands.add_parser("load-snapshot")
    snapshot.add_argument("file",type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("game_id")
    report = commands.add_parser("report")
    report.add_argument("game_id")
    report.add_argument("--workspace",required=True)
    worker = commands.add_parser("work")
    worker.add_argument("--once",action="store_true")
    worker.add_argument("--max-jobs",type=int,default=64)
    worker.add_argument("--poll",action="store_true")
    worker.add_argument("--learning-endpoint",default=os.environ.get("TEMPO_GAME_EVIDENCE_LEARNING_ENDPOINT"))
    worker.add_argument("--learning-token-file",type=Path,default=os.environ.get("TEMPO_GAME_EVIDENCE_LEARNING_TOKEN_FILE"))
    serve = commands.add_parser("serve")
    serve.add_argument("--host",default="127.0.0.1")
    serve.add_argument("--port",type=int,default=8041)
    options = parser.parse_args(arguments)
    try:
        store = EvidenceStore(options.database_url)
        if options.command == "migrate":
            # A separate owner credential is used only for this command.
            migration_url = os.environ.get("TEMPO_GAME_EVIDENCE_MIGRATION_URL") or options.database_url
            EvidenceStore(migration_url).migrate()
            result = {"migration_version":2}
        elif options.command == "import-pgn":
            if options.file.stat().st_size>MAX_SOURCE_BYTES:
                raise ValueError("source_byte_bound_exceeded")
            result = {"game_id":store.import_pgn(options.workspace,options.provider,options.source_id,options.player,options.file.read_bytes())}
        elif options.command == "import-account":
            result = {"import_id":enqueue_import(store,AccountImport.model_validate_json(options.configuration.read_bytes()))}
        elif options.command == "cancel-import":
            cancel_import(store,options.import_id)
            result = {"cancelled":options.import_id}
        elif options.command == "import-status":
            result = store.import_status(options.workspace,options.import_id)
        elif options.command == "load-snapshot":
            result = {"snapshot_digest":store.load_snapshot(ComparisonSnapshot.model_validate_json(options.file.read_bytes()))}
        elif options.command == "compare":
            store.request_comparison(options.game_id)
            result = {"requested":options.game_id}
        elif options.command == "report":
            result = store.report(options.workspace,options.game_id)
        elif options.command == "serve":
            uvicorn.run(create_app(store),host=options.host,port=options.port)
            return 0
        else:
            if not 1<=options.max_jobs<=10000 or options.once and options.poll:
                raise ValueError("invalid_worker_bounds")
            stopped = threading.Event()
            token = learning_token(options.learning_token_file) if options.learning_token_file else None
            signal.signal(signal.SIGTERM,lambda *_:stopped.set())
            signal.signal(signal.SIGINT,lambda *_:stopped.set())
            processed = 0
            while not stopped.is_set():
                progressed = False
                for operation in (lambda:import_one(store,cancelled=stopped.is_set),store.process_one,
                                  lambda:deliver_one(store,options.learning_endpoint,token=token)):
                    if stopped.is_set() or processed>=options.max_jobs:
                        break
                    if operation():
                        progressed = True
                        processed += 1
                if options.once or processed>=options.max_jobs:
                    break
                if not progressed:
                    if not options.poll:
                        break
                    stopped.wait(1)
            result = {"processed":processed,"cancelled":stopped.is_set()}
        print(json.dumps(result,sort_keys=True))
        return 0
    except Exception as error:
        # Provider responses, connection strings and raw PGNs never enter errors.
        print(json.dumps({"error":type(error).__name__,"message":"operation_failed; inspect component readiness and configuration"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
