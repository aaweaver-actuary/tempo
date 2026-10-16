"""Small supported CLI; startup never applies migrations or contacts consumers."""
import argparse
import json
import os
import sys

import psycopg
from tempo_content_publication_contracts.v1 import serialize_contract

from .domain import ContentError
from .migrate import migrate, readiness
from .store import ContentService


def parser():
    command = argparse.ArgumentParser(prog='tempo-content')
    subcommands = command.add_subparsers(dest='command',required=True)
    subcommands.add_parser('readiness')
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
    return command


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        workspace = os.environ['TEMPO_CONTENT_WORKSPACE']
        if arguments.command=='migrate':
            migrate(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL'],workspace,arguments.runtime_role)
            result = {'status':'migrated','workspace_id':workspace}
        elif arguments.command=='readiness':
            with psycopg.connect(os.environ['TEMPO_CONTENT_DATABASE_URL']) as database:
                result = readiness(database,workspace)
        else:
            with ContentService(os.environ['TEMPO_CONTENT_DATABASE_URL'],workspace) as service:
                operation = arguments.operation
                if arguments.command=='source':
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
                else:
                    result = service.publication_status(arguments.source_key)
        if hasattr(result,'model_dump'):
            print(serialize_contract(result))
        else:
            print(json.dumps(result,sort_keys=True,default=str))
        return 0
    except (ContentError,ValueError,KeyError,OSError,psycopg.Error) as error:
        # Do not print credentials or driver connection strings in diagnostics.
        code = error.code if isinstance(error,ContentError) else 'configuration_missing' if isinstance(error,KeyError) else 'database_unavailable' if isinstance(error,psycopg.Error) else 'invalid_input'
        message = str(error) if isinstance(error,(ContentError,ValueError)) and not isinstance(error,psycopg.Error) else 'Required configuration, input, or database operation is unavailable'
        print(json.dumps({'status':'error','code':code,'message':message}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
