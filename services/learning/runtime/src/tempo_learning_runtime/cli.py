"""Explicit service, bounded maintenance and owner migration commands."""
import argparse,json,os
from pathlib import Path
from .config import Configuration
from .database import Database

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    serve=commands.add_parser('serve');serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=8001)
    maintain=commands.add_parser('maintain');maintain.add_argument('--once',action='store_true',required=True)
    migration=commands.add_parser('migrate');migration.add_argument('--owner-url-file',type=Path,required=True);migration.add_argument('--workspace',required=True);migration.add_argument('--timezone',default='America/New_York')
    arguments=parser.parse_args()
    if arguments.command=='migrate':
        from .migrate import migrate
        migrate(arguments.owner_url_file.read_text().strip(),arguments.workspace,arguments.timezone)
    else:
        configuration=Configuration.environment()
        if arguments.command=='serve':
            import uvicorn
            from .api import create_app
            uvicorn.run(create_app(configuration),host=arguments.host,port=arguments.port)
        else:
            from .maintenance import run_once
            database=Database(configuration)
            try:
                with database.scope():print(json.dumps({'work_performed':run_once()}))
            finally:database.close()
if __name__=='__main__':main()
