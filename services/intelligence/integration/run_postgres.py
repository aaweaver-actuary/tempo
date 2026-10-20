"""Invocation-owned PostgreSQL proof; no external database URL is accepted."""
import hashlib
import re
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

SERVICE=Path(__file__).resolve().parent.parent


def docker(*arguments,check=True):
    return subprocess.run(["docker",*arguments],capture_output=True,text=True,check=check)


def main():
    if len(sys.argv)>2:
        raise SystemExit("only_evidence_directory_is_supported")
    destination=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else SERVICE/"test-results/native"
    destination.mkdir(parents=True,exist_ok=True)
    owner="tempo-intelligence-109-"+secrets.token_hex(8)
    volume=owner+"-data"
    report={"owner":owner,"complete":False,"container_removed":False,"volume_removed":False,"commands":[]}
    revision=subprocess.run(["git","rev-parse","HEAD"],cwd=SERVICE,capture_output=True,text=True)
    report["revision"]=revision.stdout.strip() if revision.returncode==0 else os.environ.get("TEMPO_INTELLIGENCE_TEST_REVISION","unknown")
    report["source_inputs"]={str(path.relative_to(SERVICE)):hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in (SERVICE/"src",SERVICE/"fixtures",SERVICE/"integration",SERVICE/"tests") for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts}
    created_container=created_volume=False
    private_values=[]
    try:
        image=docker("image","inspect","postgres:18.6-trixie","--format","{{.Id}}").stdout.strip()
        report["image_id"]=image
        administrator,owner_password,runtime_password=(secrets.token_urlsafe(32) for _ in range(3))
        private_values.extend([administrator,owner_password,runtime_password])
        docker("volume","create","--label","tempo.owner="+owner,"--label","tempo.disposable=true",volume)
        created_volume=True
        with tempfile.TemporaryDirectory(prefix=owner) as private:
            secret_file=Path(private)/"postgres.env"
            secret_file.write_text("POSTGRES_PASSWORD="+administrator+"\n")
            secret_file.chmod(0o600)
            docker("run","--detach","--name",owner,"--label","tempo.owner="+owner,"--label","tempo.disposable=true",
                "--memory","384m","--memory-swap","384m","--cpus","1","--pids-limit","256","--mount",f"type=volume,source={volume},target=/var/lib/postgresql",
                "--publish","127.0.0.1::5432","--env-file",str(secret_file),image,"postgres","-c","shared_buffers=32MB","-c","max_connections=40")
        created_container=True
        observed=json.loads(docker("inspect",owner,"--format","{{json .HostConfig}}").stdout)
        report["observed_limits"]={key:observed[key] for key in ("NanoCpus","Memory","MemorySwap","PidsLimit")}
        if report["observed_limits"]!={"NanoCpus":1000000000,"Memory":402653184,"MemorySwap":402653184,"PidsLimit":256}:
            raise RuntimeError("owned_postgres_limits_mismatch")
        report.update(pid=os.getpid(),pgid=os.getpgid(0),volume=volume)
        (destination/"native-start.json").write_text(json.dumps(report,indent=2)+"\n")
        port=docker("inspect",owner,"--format",'{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}').stdout.strip()
        admin=make_conninfo(host="127.0.0.1",port=port,dbname="postgres",user="postgres",password=administrator)
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
        with psycopg.connect(admin,autocommit=True) as database:
            for name,password in (("intelligence_owner",owner_password),("intelligence_runtime",runtime_password)):
                database.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(name),sql.Literal(password)))
            for name in ("intelligence","intelligence_upgrade","learning","content","game_evidence"):
                database.execute(sql.SQL("CREATE DATABASE {} OWNER intelligence_owner").format(sql.Identifier(name)))
            for name in ("postgres","template0","template1","intelligence","intelligence_upgrade","learning","content","game_evidence"):
                database.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            database.execute("GRANT CONNECT ON DATABASE intelligence TO intelligence_runtime")
        owner_url=make_conninfo(admin,dbname="intelligence",user="intelligence_owner",password=owner_password)
        runtime_url=make_conninfo(admin,dbname="intelligence",user="intelligence_runtime",password=runtime_password)
        from tempo_intelligence.migrate import migrate
        migrate(owner_url,"fixture","intelligence_runtime")
        environment={key:value for key,value in os.environ.items() if not key.startswith(("PG","TEMPO_","PYTHON","VIRTUAL_ENV","PYTEST_"))}
        environment.update(TEMPO_INTELLIGENCE_TEST_OWNER_URL=owner_url,TEMPO_INTELLIGENCE_TEST_RUNTIME_URL=runtime_url,
            TEMPO_INTELLIGENCE_TEST_ADMIN_URL=admin,TEMPO_INTELLIGENCE_TEST_CONTAINER=owner,
            TEMPO_INTELLIGENCE_TEST_UPGRADE_URL=make_conninfo(owner_url,dbname="intelligence_upgrade"),
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TEMPO_C_BROWSER_FIXTURE_FILE=str(destination/"workbench-producer.json"))
        junit=destination/"native.xml"
        command=[sys.executable,"-I","-m","pytest","-c",str(SERVICE/"pyproject.toml"),str(SERVICE/"integration/test_analysis_postgres.py"),str(SERVICE/"integration/test_postgres.py"),str(SERVICE/"integration/test_provider_postgres.py"),str(SERVICE/"integration/test_server_postgres.py"),str(SERVICE/"integration/test_games_readonly_postgres.py"),"-q","--tb=short","-o","addopts=","--junitxml="+str(junit)]
        if os.environ.get("TEMPO_C_STOCKFISH_PROFILE"):
            environment["TEMPO_C_STOCKFISH_PROFILE"]=str(Path(os.environ["TEMPO_C_STOCKFISH_PROFILE"]).resolve())
            command.insert(command.index("-q"),str(SERVICE/"integration/test_actual_analysis.py"))
        if os.environ.get("TEMPO_C_A_EXPORT") or os.environ.get("TEMPO_C_GAME_IDS_FILE"):
            for name in ('TEMPO_C_A_EXPORT','TEMPO_C_GAME_IDS_FILE','TEMPO_C_STOCKFISH_PROFILE'):
                if not os.environ.get(name):raise RuntimeError('explicit_actual_a_inputs_required')
                environment[name]=str(Path(os.environ[name]).resolve())
            command.insert(command.index('-q'),str(SERVICE/'integration/test_actual_a.py'))
        selection=os.environ.get("TEMPO_C_NATIVE_SELECTION")
        if selection=="analysis":
            command=[argument for argument in command if not argument.endswith(("/test_postgres.py","/test_provider_postgres.py","/test_server_postgres.py"))]
        elif selection=="games-readonly":
            command=[argument for argument in command if not argument.endswith(("/test_analysis_postgres.py","/test_postgres.py","/test_provider_postgres.py","/test_server_postgres.py","/test_games_readonly_postgres.py"))]
            command.insert(command.index('-q'),str(SERVICE/'integration/test_games_readonly_postgres.py')+'::test_games_readonly_http_and_transactions_preserve_all_state')
            command.insert(command.index('-q'),str(SERVICE/'integration/test_analysis_postgres.py')+'::test_C3_actual_game_read_is_exact_paginated_and_side_effect_free')
        elif selection:
            raise RuntimeError("unknown_native_selection")
        command.extend(["-o","faulthandler_timeout=45","-vv"])
        environment["PYTHONUNBUFFERED"]="1"
        for value in shlex.split(os.environ.get("PYTEST_ADDOPTS","")):
            if not value.startswith("--junitxml="):
                raise RuntimeError("only_external_junit_destination_supported")
            command.append(value)
        report["commands"].append(command)
        process=subprocess.Popen(command,env=environment,cwd=SERVICE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        with (destination/"native.log").open("w") as log:
            for line in process.stdout:
                for secret in private_values:line=line.replace(secret,"[owned-secret-redacted]")
                log.write(line);log.flush();print(line,end="",flush=True)
        returncode=process.wait()
        actual=Path(next(value.split("=",1)[1] for value in reversed(command) if value.startswith("--junitxml=")))
        if actual.exists():
            contents=actual.read_text()
            for secret in private_values:
                contents=contents.replace(secret,"[owned-secret-redacted]")
            actual.write_text(contents)
            if actual!=junit:
                shutil.copy2(actual,junit)
        cases=ET.parse(junit).getroot().findall(".//testcase") if junit.exists() else []
        report["cases"]=[case.attrib for case in cases]
        report["passed"]=sum(not any(case.find(tag) is not None for tag in ("failure","error","skipped")) for case in cases)
        if selection=="games-readonly":
            required={'test_games_readonly_http_and_transactions_preserve_all_state','test_C3_actual_game_read_is_exact_paginated_and_side_effect_free'}
            if len(cases)!=2 or {case.attrib.get('name') for case in cases}!=required:
                raise RuntimeError('games_readonly_case_inventory_mismatch')
        report["complete"]=returncode==0 and bool(cases) and report["passed"]==len(cases)
    except Exception as error:
        report["error"]=str(error) if isinstance(error,RuntimeError) and re.fullmatch("[a-z_]+",str(error)) else type(error).__name__
        if created_container:
            try:
                state=json.loads(docker("container","inspect",owner,"--format","{{json .State}}").stdout)
                report["owned_database_state"]={key:state.get(key) for key in ("Status","Running","OOMKilled","ExitCode","StartedAt","FinishedAt")}
                logs=docker("logs","--tail","100",owner,check=False)
                diagnostic=logs.stdout+logs.stderr
                for secret in private_values:diagnostic=diagnostic.replace(secret,"[owned-secret-redacted]")
                (destination/"owned-postgres.log").write_text(diagnostic)
            except Exception:report["database_diagnostic"]="unavailable"
        print("Native Intelligence proof failed: "+report["error"],file=sys.stderr)
    finally:
        try:
            for kind,name,created in (("container",owner,created_container),("volume",volume,created_volume)):
                if not created:
                    continue
                label_path="{{json .Config.Labels}}" if kind=="container" else "{{json .Labels}}"
                labels=json.loads(docker(kind,"inspect",name,"--format",label_path).stdout)
                if labels.get("tempo.owner")!=owner or labels.get("tempo.disposable")!="true":
                    raise RuntimeError("cleanup_ownership_mismatch")
                docker("rm","--force",name) if kind=="container" else docker("volume","rm",name)
                listing=["container","ls","--all"] if kind=="container" else ["volume","ls"]
                remaining=docker(*listing,"--filter","name=^"+("/" if kind=="container" else "")+name+"$","--format","{{.ID}}" if kind=="container" else "{{.Name}}").stdout.strip()
                report[kind+"_removed"]=not remaining
        except Exception as error:
            report["cleanup_error"]=type(error).__name__
        report["complete"]=report["complete"] and report["container_removed"] and report["volume_removed"]
        (destination/"native-resource.json").write_text(json.dumps(report,indent=2)+"\n")
    return 0 if report["complete"] else 1


if __name__=="__main__":
    raise SystemExit(main())
