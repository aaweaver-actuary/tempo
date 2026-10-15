"""Fresh owned cluster + volume. No caller database URL is accepted."""
import json
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg import sql

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parent


def docker(*arguments, check=True):
    return subprocess.run(["docker",*arguments],capture_output=True,text=True,check=check)


def verify_labels(kind, name, owner):
    result=docker(kind,"inspect",name,"--format","{{json .Config.Labels}}" if kind=="container" else "{{json .Labels}}")
    labels=json.loads(result.stdout)
    if labels.get("tempo.owner")!=owner or labels.get("tempo.disposable")!="true":
        raise RuntimeError("resource_ownership_not_verified")


def main():
    directory=Path(sys.argv[1]) if len(sys.argv)>1 else SERVICE/"test-results"/"native"
    directory.mkdir(parents=True,exist_ok=True)
    owner="tempo-game-evidence-41-"+secrets.token_hex(8)
    volume=owner+"-data"
    report={"owner":owner,"complete":False,"container_removed":False,"volume_removed":False,
            "commands":[],"external_database_accepted":False}
    report["revision"]=subprocess.check_output(["git","rev-parse","HEAD"],cwd=SERVICE,text=True).strip()
    report["source_inputs"]={str(path.relative_to(SERVICE)):hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in (SERVICE/"src",SERVICE/"fixtures",HERE) for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts}
    learning=SERVICE.parent/"learning/remediation"
    report["receiver_inputs"]={str(path.relative_to(learning)):hashlib.sha256(path.read_bytes()).hexdigest()
        for name in ("src","migrations") for path in (learning/name).rglob("*") if path.is_file() and "__pycache__" not in path.parts}
    created_container=created_volume=False
    secrets_to_redact=[]
    try:
        image=docker("image","inspect","postgres:18.6-trixie","--format","{{.Id}}").stdout.strip()
        report["image"]=image
        password=secrets.token_urlsafe(32)
        secrets_to_redact.append(password)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1",0))
            published_port=reservation.getsockname()[1]
        docker("volume","create","--label","tempo.owner="+owner,"--label","tempo.disposable=true",volume)
        created_volume=True
        with tempfile.TemporaryDirectory(prefix=owner) as secret_directory:
            secret=Path(secret_directory)/"postgres.env"
            secret.write_text("POSTGRES_PASSWORD="+password+"\n")
            secret.chmod(0o600)
            docker("run","--detach","--name",owner,"--label","tempo.owner="+owner,
                   "--label","tempo.disposable=true","--memory","384m","--cpus","1","--pids-limit","256",
                   "--mount",f"type=volume,source={volume},target=/var/lib/postgresql",
                   "--publish",f"127.0.0.1:{published_port}:5432","--env-file",str(secret),image,
                   "postgres","-c","shared_buffers=32MB","-c","max_connections=24")
        created_container=True
        port=docker("inspect",owner,"--format",'{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}').stdout.strip()
        admin=make_conninfo(host="127.0.0.1",port=port,dbname="postgres",user="postgres",password=password)
        deadline=time.monotonic()+30
        while True:
            try:
                with psycopg.connect(admin,connect_timeout=1) as database:
                    database.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic()>deadline:
                    raise RuntimeError("owned_postgres_not_ready") from None
                time.sleep(.1)
        owner_password,runtime_password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        secrets_to_redact.extend([owner_password,runtime_password])
        with psycopg.connect(admin,autocommit=True) as database:
            for name,secret_value in (("evidence_owner",owner_password),("evidence_runtime",runtime_password)):
                database.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(name),sql.Literal(secret_value)))
            database.execute("CREATE DATABASE game_evidence OWNER evidence_owner")
            database.execute("CREATE DATABASE evidence_upgrade OWNER evidence_owner")
            database.execute("REVOKE ALL ON DATABASE evidence_upgrade FROM PUBLIC")
            database.execute("CREATE DATABASE content_guard")
            for name in ("postgres","template1","content_guard","game_evidence"):
                database.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            database.execute("GRANT CONNECT ON DATABASE game_evidence TO evidence_runtime")
        migration_url=make_conninfo(admin,dbname="game_evidence",user="evidence_owner",password=owner_password)
        runtime_url=make_conninfo(admin,dbname="game_evidence",user="evidence_runtime",password=runtime_password)
        from tempo_game_evidence import EvidenceStore
        EvidenceStore(migration_url).migrate()
        with psycopg.connect(migration_url) as database:
            database.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            database.execute("GRANT USAGE ON SCHEMA public TO evidence_runtime")
            database.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON workspace_inputs,snapshots,games,jobs,game_sources,comparisons,outbox,import_cursors,import_pages,provider_leases,accepted_heads TO evidence_runtime")
            database.execute("GRANT SELECT ON evidence_schema_migrations TO evidence_runtime")
        learning_owner_password,learning_runtime_password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        secrets_to_redact.extend([learning_owner_password,learning_runtime_password])
        with psycopg.connect(admin,autocommit=True) as database:
            for role,secret_value in (("remediation_owner",learning_owner_password),("remediation_runtime",learning_runtime_password)):
                database.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role),sql.Literal(secret_value)))
            database.execute("CREATE DATABASE learning_fixture OWNER remediation_owner")
            database.execute("REVOKE ALL ON DATABASE learning_fixture FROM PUBLIC")
            database.execute("GRANT CONNECT ON DATABASE learning_fixture TO remediation_runtime")
        learning_migration=make_conninfo(admin,dbname="learning_fixture",user="remediation_owner",password=learning_owner_password)
        learning_runtime=make_conninfo(admin,dbname="learning_fixture",user="remediation_runtime",password=learning_runtime_password)
        from importlib.resources import files
        with psycopg.connect(learning_migration) as database:
            database.execute(files("tempo_learning_remediation").joinpath("migrations/001_remediation.sql").read_text())
            database.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            database.execute("GRANT USAGE ON SCHEMA public TO remediation_runtime")
            tables=database.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            for (table,) in tables:
                database.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON {} TO remediation_runtime").format(sql.Identifier(table)))
        environment={key:value for key,value in os.environ.items()
                     if not key.startswith(("PG","TEMPO_DATABASE","TEMPO_GAME_EVIDENCE","PYTHONPATH"))}
        environment.update(TEMPO_GAME_EVIDENCE_DATABASE_URL=runtime_url,
                           TEMPO_GAME_EVIDENCE_TEST_CONTAINER=owner,
                           TEMPO_GAME_EVIDENCE_MIGRATION_URL=migration_url,
                           TEMPO_GAME_EVIDENCE_TEST_LEARNING_URL=learning_runtime,
                           TEMPO_GAME_EVIDENCE_TEST_UPGRADE_URL=make_conninfo(migration_url,dbname="evidence_upgrade"),
                           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        junit=directory/"native.xml"
        command=[sys.executable,"-m","pytest","-c",str(SERVICE/"pyproject.toml"),str(HERE/"test_postgres.py"),
                 "-q","--tb=short","-o","addopts=","--junitxml="+str(junit)]
        if len(sys.argv)>2:
            command.extend(["-k",sys.argv[2]])
        external_addopts=os.environ.get("PYTEST_ADDOPTS")
        if external_addopts:
            # The existing component lane supplies its own JUnit destination.
            import shlex
            for value in shlex.split(external_addopts):
                if not value.startswith("--junitxml="):
                    raise ValueError("only_external_junit_destination_supported")
            command.extend(shlex.split(external_addopts))
            environment.pop("PYTEST_ADDOPTS",None)
        report["commands"].append(command)
        result=subprocess.run(command,env=environment,cwd=SERVICE,capture_output=True,text=True)
        output=result.stdout+result.stderr
        for secret_value in secrets_to_redact:
            output=output.replace(secret_value,"[disposable-secret-redacted]")
        for report_file in [junit, *[Path(value.split("=",1)[1]) for value in command if value.startswith("--junitxml=")]]:
            if report_file.exists():
                text=report_file.read_text()
                for secret_value in secrets_to_redact:
                    text=text.replace(secret_value,"[disposable-secret-redacted]")
                report_file.write_text(text)
        (directory/"native.log").write_text(output)
        print(output,end="")
        report["pytest_exit_code"]=result.returncode
        actual_junit=Path(next(value.split("=",1)[1] for value in reversed(command) if value.startswith("--junitxml=")))
        if actual_junit.exists() and actual_junit!=junit:
            shutil.copy2(actual_junit,junit)
        cases=ET.parse(actual_junit).getroot().findall(".//testcase") if actual_junit.exists() else []
        report["passed"]=sum(not any(case.find(tag) is not None for tag in ("failure","error","skipped")) for case in cases)
        report["complete"]=result.returncode==0 and bool(cases) and report["passed"]==len(cases)
    except Exception as error:
        report["error"]=type(error).__name__
        print("Owned native proof failed: "+type(error).__name__,file=sys.stderr)
    finally:
        try:
            if created_container:
                verify_labels("container",owner,owner)
                docker("rm","--force",owner)
                remaining=docker("container","ls","--all","--filter","name=^/"+owner+"$","--format","{{.ID}}").stdout.strip()
                report["container_removed"]=not remaining
            if created_volume:
                verify_labels("volume",volume,owner)
                docker("volume","rm",volume)
                report["volume_removed"]=not docker("volume","ls","--filter","name=^"+volume+"$","--format","{{.Name}}").stdout.strip()
        except Exception as error:
            report["cleanup_error"]=type(error).__name__
        report["complete"]=report["complete"] and report["container_removed"] and report["volume_removed"]
        (directory/"native-resource.json").write_text(json.dumps(report,indent=2)+"\n")
    return 0 if report["complete"] else 1


if __name__=="__main__":
    raise SystemExit(main())
