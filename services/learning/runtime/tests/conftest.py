from dataclasses import dataclass
from pathlib import Path
import json,os,uuid
import httpx,psycopg,pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient
from tempo_learning_runtime import Configuration,create_app
from tempo_learning_runtime.migrate import migrate,grant_runtime

@dataclass
class Case:
    client:TestClient
    database:object
    admin:str
    configuration:Configuration
    def query(self,statement,parameters=()):
        with self.database.scope(),self.database.transaction() as database:return database.execute_native(statement,parameters).fetchall() if not statement.startswith(('UPDATE','INSERT','DELETE')) else database.execute_native(statement,parameters).rowcount

@pytest.fixture
def case():
    identity=os.environ.get('TEMPO_LEARNING_TEST_FIXTURE','')
    if not identity.startswith('tempo-learning-'):raise RuntimeError('Run the owning disposable PostgreSQL runner; external databases are refused')
    admin=os.environ['TEMPO_LEARNING_TEST_ADMIN'];name='learning_'+uuid.uuid4().hex[:12]
    writer=name+'_writer';reader=name+'_reader';owner=name+'_owner';password=uuid.uuid4().hex
    with psycopg.connect(admin,autocommit=True) as database:
        database.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(owner)))
        database.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}').format(sql.Identifier(writer),sql.Literal(password)))
        database.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}').format(sql.Identifier(reader),sql.Literal(password)))
        database.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(reader_role)))
        database.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(owner)))
        database.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        for role in [writer,reader]:database.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name),sql.Identifier(role)))
    owner_url=make_conninfo(admin,dbname=name,options='-c role='+owner)
    migrate(owner_url,'workspace-a','America/New_York');grant_runtime(owner_url,writer,reader)
    configuration=Configuration('workspace-a',make_conninfo(admin,dbname=name,user=writer,password=password),make_conninfo(admin,dbname=name,user=reader,password=password),'test-credential-'+'x'*32,maintenance=False,tablebase_url=None)
    app=create_app(configuration)
    with TestClient(app,headers={'Authorization':'Bearer '+configuration.token,'Origin':configuration.origins[0]}) as client:
        yield Case(client,app.state.database,make_conninfo(admin,dbname=name),configuration)
    with psycopg.connect(admin,autocommit=True) as database:
        database.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        for role in [writer,reader,owner]:database.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
