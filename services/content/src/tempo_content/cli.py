"""Small supported CLI; startup never applies migrations or contacts consumers."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from tempo_content_publication_contracts.v1 import serialize_contract

from .domain import ContentError
from tempo_content_database.ddl.migrate import DatabaseError, migrate, readiness_from_url
from tempo_content_database.adapters.store import ContentService


def parser():
    command = argparse.ArgumentParser(prog='tempo-content')
    subcommands = command.add_subparsers(dest='command',required=True)
    subcommands.add_parser('readiness')
    schema = subcommands.add_parser('schema')
    schema.add_argument('--model',choices=('import-manifest','response-proposal'),required=True)
    importing = subcommands.add_parser('import').add_subparsers(dest='operation',required=True)
    manifest = importing.add_parser('manifest')
    manifest.add_argument('file',type=Path)
    manifest.add_argument('--workspace',required=True)
    manifest.add_argument('--source-key',required=True)
    manifest.add_argument('--title',required=True)
    manifest.add_argument('--color',choices=('white','black'),required=True)
    manifest.add_argument('--output',type=Path,required=True)
    plan = importing.add_parser('plan')
    plan.add_argument('file',type=Path)
    plan.add_argument('--manifest',type=Path,required=True)
    plan.add_argument('--expected-revision',type=int,required=True)
    proposal = subcommands.add_parser('proposal').add_subparsers(dest='operation',required=True)
    proposal.add_parser('inspect').add_argument('file',type=Path)
    drafts = subcommands.add_parser('draft').add_subparsers(dest='operation',required=True)
    drafts.add_parser('create').add_argument('--proposal',type=Path,required=True)
    drafts.add_parser('show').add_argument('identifier')
    approval = drafts.add_parser('approve')
    approval.add_argument('identifier')
    approval.add_argument('--digest',required=True)
    operations = subcommands.add_parser('operation').add_subparsers(dest='operation',required=True)
    for name in ('show','cancel','prepare','record','record-failure','checkpoint','fail-before-delivery'):
        operation = operations.add_parser(name)
        operation.add_argument('identifier')
        if name=='prepare':
            operation.add_argument('--max-slices',type=int,default=64)
        if name in ('record','checkpoint'):
            operation.add_argument('--file',type=Path,required=True)
        if name in ('record','record-failure'):
            operation.add_argument('--learning-origin',required=True)
            operation.add_argument('--operator-token-file',type=Path,required=True)
    migration = subcommands.add_parser('migrate')
    migration.add_argument('--runtime-role',default=os.environ.get('TEMPO_CONTENT_RUNTIME_ROLE','content_runtime'))
    source = subcommands.add_parser('source').add_subparsers(dest='operation',required=True)
    load = source.add_parser('load')
    load.add_argument('file')
    load.add_argument('--expected-revision',type=int,required=True)
    source.add_parser('show').add_argument('source_key')
    repertoire = subcommands.add_parser('repertoire').add_subparsers(dest='operation',required=True)
    show = repertoire.add_parser('show')
    show.add_argument('repertoire_key')
    show.add_argument('--source-key')
    inventory = subcommands.add_parser('inventory').add_subparsers(dest='operation',required=True)
    for name in ('request','status'):
        inventory.add_parser(name).add_argument('source_key')
    worker = inventory.add_parser('work')
    worker.add_argument('--source-key')
    bounds = worker.add_mutually_exclusive_group()
    bounds.add_argument('--once',action='store_true')
    bounds.add_argument('--max-slices',type=int,default=1)
    for name in ('positions','routes'):
        reader = inventory.add_parser(name)
        reader.add_argument('source_key')
        reader.add_argument('--limit',type=int,default=64)
        reader.add_argument('--after')
    replies = inventory.add_parser('replies')
    replies.add_argument('position_key')
    replies.add_argument('--limit',type=int,default=64)
    replies.add_argument('--after')
    coverage = inventory.add_parser('coverage')
    coverage.add_argument('source_key')
    coverage.add_argument('route_key')
    coverage.add_argument('policy_key')
    coverage.add_argument('ply',type=int)
    publication = subcommands.add_parser('publication').add_subparsers(dest='operation',required=True)
    for name in ('build','status'):
        publication.add_parser(name).add_argument('source_key')
    show = publication.add_parser('show')
    show.add_argument('publication_id')
    show.add_argument('--generation',type=int)
    export = publication.add_parser('export')
    export.add_argument('publication_id')
    export.add_argument('--generation',type=int,required=True)
    return command


def _manifest_preview(arguments):
    from .bridge import map_import
    from .import_manifest import make_manifest

    if not arguments.output.parent.is_dir():
        raise ContentError('invalid_output_directory','Manifest output directory must already exist')
    with arguments.file.open('rb') as source_file:
        raw = source_file.read(16 * 1024 * 1024 + 1)
    if len(raw)>16*1024*1024:
        raise ContentError('source_byte_bound','Import exceeds 16 MiB')
    manifest = make_manifest(raw,workspace_id=arguments.workspace,source_key=arguments.source_key,
                             title=arguments.title,learner_color=arguments.color)
    source,manifest = map_import(raw,manifest.model_dump(mode='json'))
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=arguments.output.parent,
                                     prefix='.tempo-content-manifest-',suffix='.tmp') as temporary:
        temporary.write(manifest.model_dump_json(indent=2)+'\n')
        temporary.flush()
        os.fsync(temporary.fileno())
        # A hard link publishes the complete file atomically and refuses any existing path.
        os.link(temporary.name,arguments.output)
    return {'status':'validated','manifest_path':str(arguments.output),
            'source_sha256':manifest.provenance.source_sha256,
            'route_count':len(source.routes),'exercise_count':len(source.exercises)}


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        if arguments.command=='import' and arguments.operation=='manifest':
            print(json.dumps(_manifest_preview(arguments),sort_keys=True))
            return 0
        if arguments.command in ('schema','proposal'):
            from .bridge import ImportManifest,ResponseProposal
            result = (ImportManifest if arguments.model=='import-manifest' else ResponseProposal).model_json_schema() if arguments.command=='schema' else ResponseProposal.model_validate_json(arguments.file.read_bytes()).model_dump(mode='json')
            print(json.dumps(result,sort_keys=True))
            return 0
        workspace = os.environ['TEMPO_CONTENT_WORKSPACE']
        if arguments.command=='migrate':
            migrate(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL'],workspace,arguments.runtime_role)
            result = {'status':'migrated','workspace_id':workspace}
        elif arguments.command=='readiness':
            result = readiness_from_url(os.environ['TEMPO_CONTENT_DATABASE_URL'],workspace)
        else:
            with ContentService(os.environ['TEMPO_CONTENT_DATABASE_URL'],workspace) as service:
                operation = arguments.operation
                from .bridge import Bridge, ResponseProposal, map_import
                bridge = Bridge(service)
                if arguments.command=='import':
                    if arguments.file.stat().st_size>16*1024*1024:
                        raise ContentError('source_byte_bound','Import exceeds 16 MiB')
                    source,manifest = map_import(arguments.file.read_bytes(),json.loads(arguments.manifest.read_text()))
                    result = bridge.draft(source.model_dump(mode='json'),manifest.model_dump(mode='json'),arguments.expected_revision)
                elif arguments.command=='proposal':
                    result = ResponseProposal.model_validate_json(arguments.file.read_bytes()).model_dump(mode='json')
                elif arguments.command=='draft':
                    result = bridge.proposal_draft(json.loads(arguments.proposal.read_text())) if operation=='create' else bridge.approve(arguments.identifier,arguments.digest) if operation=='approve' else bridge.draft_show(arguments.identifier)
                elif arguments.command=='operation':
                    if operation=='prepare' and not 1<=arguments.max_slices<=10000:
                        raise ContentError('invalid_slice_bound','max-slices must be 1..10000')
                    if operation=='record-failure':
                        from .receipt_transport import verified_terminal_failure
                        verified=verified_terminal_failure(bridge.operation(arguments.identifier),arguments.learning_origin,arguments.operator_token_file)
                        result=bridge.record_terminal_failure(arguments.identifier,verified)
                    elif operation=='record':
                        from .receipt_transport import verified_receipt
                        verified=verified_receipt(bridge.operation(arguments.identifier),json.loads(arguments.file.read_text()),arguments.learning_origin,arguments.operator_token_file)
                        result=bridge.record(arguments.identifier,verified)
                    elif operation=='fail-before-delivery':
                        result=bridge.fail_before_delivery(arguments.identifier)
                    else:
                        result = bridge.prepare(arguments.identifier,arguments.max_slices) if operation=='prepare' else bridge.checkpoint(arguments.identifier,json.loads(arguments.file.read_text())) if operation=='checkpoint' else bridge.cancel(arguments.identifier) if operation=='cancel' else bridge.operation(arguments.identifier)
                elif arguments.command=='source':
                    if operation=='load':
                        with open(arguments.file,encoding='utf-8') as source_file:
                            result = service.load_source(json.load(source_file),arguments.expected_revision)
                    else:
                        result = service.source_show(arguments.source_key)
                elif arguments.command=='repertoire':
                    result = service.repertoire_show(arguments.repertoire_key,arguments.source_key)
                elif arguments.command=='inventory':
                    if operation=='work':
                        slices = 1 if arguments.once else arguments.max_slices
                        if not 1<=slices<=10000:
                            raise ContentError('invalid_slice_bound','max-slices must be 1..10000')
                        deliveries = []
                        for unused in range(slices):
                            delivery = service.work_once(arguments.source_key)
                            deliveries.append(delivery)
                            if delivery['status'] in ('failed','idle','stale_generation'):
                                break
                        result = {'deliveries':deliveries,'slices':len(deliveries)}
                    elif operation in ('positions','routes'):
                        result = getattr(service,operation)(arguments.source_key,arguments.limit,arguments.after)
                    elif operation=='replies':
                        result = service.replies(arguments.position_key,arguments.limit,arguments.after)
                    elif operation=='coverage':
                        result = service.coverage(arguments.source_key,arguments.route_key,arguments.policy_key,arguments.ply)
                    else:
                        result = getattr(service,'request_inventory' if operation=='request' else 'inventory_status')(arguments.source_key)
                elif operation=='build':
                    result = service.publication_build(arguments.source_key)
                elif operation=='show':
                    result = service.publication_show(arguments.publication_id,arguments.generation)
                elif operation=='export':
                    result = bridge.export(arguments.publication_id,arguments.generation)
                else:
                    result = service.publication_status(arguments.source_key)
        if hasattr(result,'model_dump'):
            print(serialize_contract(result))
        else:
            print(json.dumps(result,sort_keys=True,default=str))
        return 0
    except (ContentError,ValueError,KeyError,OSError,DatabaseError) as error:
        # Do not print credentials or driver connection strings in diagnostics.
        code = error.code if isinstance(error,ContentError) else 'configuration_missing' if isinstance(error,KeyError) else 'database_unavailable' if isinstance(error,DatabaseError) else 'invalid_input'
        message = str(error) if isinstance(error,(ContentError,ValueError)) and not isinstance(error,DatabaseError) else 'Required configuration, input, or database operation is unavailable'
        print(json.dumps({'status':'error','code':code,'message':message}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
