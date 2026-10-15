"""One invocation-owned PostgreSQL cluster; never accepts a database URL."""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from qualification import controlled_environment, junit_destination, validate_inventory


def docker(*arguments, check=True):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, check=check)


def main():
    external_junit = junit_destination(os.environ.get("PYTEST_ADDOPTS", ""), allow_junit=True)
    identity = "tempo224-" + secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    image = docker("image", "inspect", "postgres:18.6-trixie", "--format", "{{.Id}}").stdout.strip()
    report_directory = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else Path(os.environ.get("TEMPO_LEARNING_EVIDENCE_DIR", str(ROOT / "test-results/learning-review-224"))).resolve()
    report_directory.mkdir(parents=True, exist_ok=True)
    started = False
    try:
        with tempfile.TemporaryDirectory(prefix="tempo224-secret-") as secret_directory:
            secret_file = Path(secret_directory) / "postgres.env"
            secret_file.write_text("POSTGRES_PASSWORD=" + password + "\n")
            secret_file.chmod(0o600)
            docker("run", "--detach", "--name", identity, "--label", "tempo.owner=" + identity,
                   "--label", "tempo.disposable=true", "--memory", "384m", "--cpus", "1",
                   "--pids-limit", "256", "--tmpfs", "/var/lib/postgresql:rw,size=512m",
                   "--publish", "127.0.0.1::5432", "--env-file", str(secret_file), image,
                   "postgres", "-c", "shared_buffers=32MB", "-c", "max_connections=32")
            started = True
        port = docker("inspect", identity, "--format", '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}').stdout.strip()
        import psycopg
        from psycopg.conninfo import make_conninfo
        admin = make_conninfo(host="127.0.0.1",port=port,dbname="postgres",user="postgres",password=password)
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(admin, connect_timeout=1) as ready:
                    ready.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Owned PostgreSQL TCP endpoint did not become ready") from None
                time.sleep(.1)
        sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "backend")]
        from apply_postgres_migrations import apply_migrations
        from provision_postgres_roles import provision
        apply_migrations(admin)
        reader_password, writer_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        provision(admin, reader_password, writer_password)
        with psycopg.connect(admin) as database:
            database.execute("INSERT INTO settings(id) VALUES(1)")
        environment = {key:value for key,value in controlled_environment().items()
                       if not key.startswith(("TEMPO_DATABASE_", "TEMPO224_", "PG", "TEMPO_REDIS_URL", "CELERY_"))}
        environment.update(TEMPO224_FIXTURE=identity, TEMPO224_ADMIN=admin,
            TEMPO_DATABASE_READ_URL=make_conninfo(admin,user="tempo_reader",password=reader_password),
            TEMPO_DATABASE_WRITE_URL=make_conninfo(admin,user="tempo_writer",password=writer_password),
            TEMPO_TEST_INSTANCE="disposable", TEMPO_POSTGRES_POOL_SIZE="8",
            TEMPO_REDIS_URL="redis://127.0.0.1:1/0",
            PYTHONPATH=os.pathsep.join((str(ROOT/"backend"),str(ROOT/"scripts"))))
        junit = external_junit or report_directory/"native.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        helper = HERE.parent/"qualification.py"
        arguments = ["-c",str(HERE.parent/"pyproject.toml"),"-o","addopts=",
                     "-p","no:cacheprovider",str(HERE/"test_postgres.py")]
        collection = report_directory/"native-collected.json"
        execution = report_directory/"native-executed.json"
        collected = subprocess.run([sys.executable,str(helper),str(collection),*arguments,"--collect-only","-q"],
            env=environment, cwd=ROOT, capture_output=True,text=True)
        (report_directory/"native-collection.log").write_text(collected.stdout+collected.stderr)
        if collected.returncode:
            raise RuntimeError("Native inventory collection failed; see native-collection.log")
        result = subprocess.run([sys.executable,str(helper),str(execution),*arguments,
            "-v","--tb=short","--junitxml="+str(junit)],
            env=environment, cwd=ROOT, capture_output=True,text=True)
        (report_directory/"native.log").write_text(result.stdout + result.stderr)
        print(result.stdout)
        if result.returncode:
            print(result.stderr)
        returncode = result.returncode
        if external_junit and junit.exists() and junit != report_directory/"native.xml":
            (report_directory/"native.xml").write_bytes(junit.read_bytes())
        identities = validate_inventory(json.loads(collection.read_text()), json.loads(execution.read_text()), junit)
        (report_directory/"native.json").write_text(json.dumps({
            "revision":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
            "runtime":sys.version,"identities":identities,"passed":len(identities),
            "external_junit":str(external_junit) if external_junit else None,
            "commands":[collected.args,result.args],"complete":True},indent=2)+"\n")
    finally:
        if started:
            owner = docker("inspect",identity,"--format",'{{index .Config.Labels "tempo.owner"}}').stdout.strip()
            if owner != identity:
                raise RuntimeError("Refusing cleanup after ownership mismatch")
            docker("rm","--force",identity)
            if docker("inspect",identity,check=False).returncode == 0:
                raise RuntimeError("Owned test container survived cleanup")
        (report_directory/"native-resource.json").write_text(json.dumps({
            "identity":identity,"image":image,"container_removed":started,
            "exit_code":locals().get("returncode"),"external_database_accepted":False},indent=2)+"\n")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
