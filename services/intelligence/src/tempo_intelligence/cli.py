"""Local bounded operations; secrets are supplied only by private files."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from pydantic import ValidationError
from tempo_analysis_jobs_contracts.v1 import MaiaResult, JobStatus

from .api import CredentialCommand, RevisionCommand, create_app
from .config import Configuration, private_text
from .credentials import CredentialError
from .errors import DomainError
from .models import RequestInput

MAX_INPUT_BYTES = 1024 * 1024


def read_document(path):
    if path is None or str(path) == "-":
        value = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        with Path(path).open("rb") as source:
            value = source.read(MAX_INPUT_BYTES + 1)
    if len(value) > MAX_INPUT_BYTES:
        raise ValueError("input_size_exceeded")
    return value


def parser():
    command = argparse.ArgumentParser(prog="tempo-intelligence")
    commands = command.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8049)
    migration = commands.add_parser("migrate")
    migration.add_argument("--owner-url-file", type=Path, required=True)
    migration.add_argument("--workspace", required=True)
    migration.add_argument("--runtime-role")
    work = commands.add_parser("work")
    work.add_argument("--once", action="store_true", required=True)
    for name in ("request", "accept-maia", "maia-status"):
        operation = commands.add_parser(name)
        operation.add_argument("--file", type=Path, default=Path("-"))
    for name in ("inspect", "maia-jobs", "refresh", "retry"):
        operation = commands.add_parser(name)
        operation.add_argument("request_id")
        if name in {"refresh", "retry"}:
            operation.add_argument("--file", type=Path, default=Path("-"))
    evidence = commands.add_parser("evidence")
    evidence.add_argument("evidence_id")
    credentials = commands.add_parser("credentials")
    credential_commands = credentials.add_subparsers(dest="credential_command", required=True)
    for name in ("reload", "revoke"):
        operation = credential_commands.add_parser(name)
        operation.add_argument("--file", type=Path, default=Path("-"))
    from .analysis.cli import parser as analysis_parser
    analysis_parser(commands)
    return command


async def bounded_work(store, configuration):
    from .credentials import PrivateCredentials
    from .provider import ExplorerClient
    from .worker import Worker

    client = ExplorerClient(configuration.provider_base_url)
    worker = Worker(store, PrivateCredentials(configuration.credential_file), client)
    try:
        return {"work_performed": await worker.work_once()}
    finally:
        await worker.aclose()
        if hasattr(client, "aclose"):
            await client.aclose()


def main(arguments=None):
    options = parser().parse_args(arguments)
    store = None
    try:
        if options.command == "migrate":
            from tempo_intelligence_database.ddl.migrate import migrate

            migrate(private_text(options.owner_url_file), options.workspace, options.runtime_role)
            result = {"migration": "complete", "workspace_id": options.workspace}
        else:
            configuration = Configuration.environment()
            if options.command == "serve":
                import uvicorn

                uvicorn.run(create_app(configuration), host=options.host, port=options.port, access_log=False)
                return 0
            from tempo_intelligence_database.adapters.store import Store

            store = Store(configuration)
            store.validate_startup()
            if options.command == "analysis":
                from .analysis.cli import execute
                result = execute(options, store, read_document)
            elif options.command == "work":
                result = asyncio.run(bounded_work(store, configuration))
            elif options.command == "request":
                result = store.submit(RequestInput.model_validate_json(read_document(options.file)))
            elif options.command == "inspect":
                result = store.inspect(options.request_id)
            elif options.command == "evidence":
                result = store.evidence(options.evidence_id)
            elif options.command == "maia-jobs":
                result = store.maia_jobs(options.request_id)
            elif options.command in {"refresh", "retry"}:
                payload = RevisionCommand.model_validate_json(read_document(options.file))
                result = getattr(store, options.command)(options.request_id, payload.command_id, payload.expected_revision)
            elif options.command == "accept-maia":
                result = store.accept_maia(MaiaResult.model_validate_json(read_document(options.file)))
            elif options.command == "maia-status":
                result = store.maia_status(JobStatus.model_validate_json(read_document(options.file)))
            else:
                payload = CredentialCommand.model_validate_json(read_document(options.file))
                if options.credential_command == "revoke":
                    result = store.credential_revoke(payload.expected_generation)
                else:
                    from .credentials import PrivateCredentials

                    result = store.credential_reload(PrivateCredentials(configuration.credential_file).load(), payload.expected_generation)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except (DomainError, CredentialError) as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 1
    except ValidationError:
        print(json.dumps({"error": "invalid_request"}), file=sys.stderr)
        return 1
    except Exception:
        # Driver/provider errors may contain DSNs or authorization material.
        print(json.dumps({"error": "intelligence_operation_failed"}), file=sys.stderr)
        return 1
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                print(json.dumps({"error": "intelligence_shutdown_failed"}), file=sys.stderr)
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
