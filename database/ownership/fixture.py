"""Fresh PostgreSQL-only fixture. Never accepts an external/live database URL."""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

import psycopg
from psycopg import sql

DOMAINS = ("learning", "content", "game_evidence", "intelligence")
IMAGE = "postgres:18.6-trixie"


def docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=check
    )


class OwnershipFixture:
    def __init__(self):
        self.identity = "tempo264-" + uuid.uuid4().hex
        self.container = self.identity + "-postgres"
        self.port = None
        self.admin_password = secrets.token_urlsafe(32)
        self.passwords = {domain: secrets.token_urlsafe(32) for domain in DOMAINS}
        self.image_id = None
        self.started = False

    def start(self):
        self.image_id = docker(
            "image", "inspect", IMAGE, "--format", "{{.Id}}"
        ).stdout.strip()
        # An exact local image ID makes the fixture independent of mutable tags.
        with tempfile.TemporaryDirectory(
            prefix="tempo264-db-secret-"
        ) as secret_directory:
            secret_path = Path(secret_directory) / "postgres.env"
            secret_path.write_text("POSTGRES_PASSWORD=" + self.admin_password + "\n")
            secret_path.chmod(0o600)
            docker(
                "run",
                "--detach",
                "--name",
                self.container,
                "--label",
                "tempo.contract-owner=" + self.identity,
                "--label",
                "tempo.disposable=true",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                "--tmpfs",
                "/var/lib/postgresql:rw,size=256m",
                "--publish",
                "127.0.0.1::5432",
                "--env-file",
                str(secret_path),
                self.image_id,
                "postgres",
                "-c",
                "max_connections=32",
                "-c",
                "shared_buffers=32MB",
            )
            self.started = True
        binding = json.loads(
            docker(
                "inspect", self.container, "--format", "{{json .NetworkSettings.Ports}}"
            ).stdout
        )
        self.port = int(binding["5432/tcp"][0]["HostPort"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready = docker(
                "exec", self.container, "pg_isready", "-U", "postgres", check=False
            )
            if ready.returncode == 0:
                try:
                    with self.admin("postgres") as connection:
                        connection.execute("SELECT 1")
                    break
                except psycopg.OperationalError:
                    pass
            time.sleep(0.1)
        else:
            raise RuntimeError(
                "owned PostgreSQL did not become ready within 30 seconds"
            )
        self.provision()

    def connection(self, database, role, password):
        return psycopg.connect(
            host="127.0.0.1",
            port=self.port,
            dbname=database,
            user=role,
            password=password,
            autocommit=True,
            connect_timeout=3,
        )

    def admin(self, database):
        return self.connection(database, "postgres", self.admin_password)

    def runtime(self, domain, database=None):
        return self.connection(
            database or domain, domain + "_runtime", self.passwords[domain]
        )

    def provision(self):
        with self.admin("postgres") as administration:
            for domain in DOMAINS:
                owner = domain + "_owner"
                runtime = domain + "_runtime"
                administration.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(owner))
                )
                administration.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(
                        sql.Identifier(runtime), sql.Literal(self.passwords[domain])
                    )
                )
                administration.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(domain), sql.Identifier(owner)
                    )
                )
                administration.execute(
                    sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(domain)
                    )
                )
                administration.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(domain), sql.Identifier(runtime)
                    )
                )
        for domain in DOMAINS:
            owner = domain + "_owner"
            runtime = domain + "_runtime"
            with self.admin(domain) as connection:
                connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                connection.execute(
                    sql.SQL("CREATE SCHEMA domain AUTHORIZATION {}").format(
                        sql.Identifier(owner)
                    )
                )
                connection.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA domain TO {}").format(
                        sql.Identifier(runtime)
                    )
                )
                connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))
                connection.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA domain REVOKE ALL ON TABLES FROM PUBLIC"
                    )
                )
                connection.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA domain REVOKE ALL ON SEQUENCES FROM PUBLIC"
                    )
                )
                connection.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                    )
                )
                connection.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA domain GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}"
                    ).format(sql.Identifier(runtime))
                )
                connection.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA domain GRANT USAGE,SELECT ON SEQUENCES TO {}"
                    ).format(sql.Identifier(runtime))
                )
                connection.execute(
                    "CREATE TABLE domain.settings (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, setting_key text UNIQUE NOT NULL, setting_value text NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE domain.migration_history (revision integer PRIMARY KEY)"
                )
                connection.execute(
                    sql.SQL(
                        "REVOKE INSERT,UPDATE,DELETE ON domain.migration_history FROM {}"
                    ).format(sql.Identifier(runtime))
                )
                connection.execute("INSERT INTO domain.migration_history VALUES (1)")
                connection.execute("RESET ROLE")

    def close(self):
        if not self.started:
            return
        labels = json.loads(
            docker(
                "inspect", self.container, "--format", "{{json .Config.Labels}}"
            ).stdout
        )
        if (
            labels.get("tempo.contract-owner") != self.identity
            or labels.get("tempo.disposable") != "true"
        ):
            raise RuntimeError("ownership changed; refusing fixture teardown")
        docker("rm", "--force", self.container)
        if docker(
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=tempo.contract-owner=" + self.identity,
        ).stdout.strip():
            raise RuntimeError("owned fixture container remains")
        if docker(
            "volume",
            "ls",
            "--quiet",
            "--filter",
            "label=tempo.contract-owner=" + self.identity,
        ).stdout.strip():
            raise RuntimeError("owned fixture volume remains")
        self.started = False
        print("Verified cleanup: " + self.identity, flush=True)


def assert_component_credential(connection, domain):
    role = connection.execute("SELECT current_user").fetchone()[0]
    if role != domain + "_runtime":
        raise ValueError("component credential is not its isolated runtime role")
    flags = connection.execute(
        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if any(flags):
        raise ValueError("component role has administrative or inherited privileges")
    if connection.execute(
        "SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
    ).fetchone():
        raise ValueError("component role has undeclared role membership")
    ownership = connection.execute(
        "SELECT current_database(), pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
    ).fetchone()
    if ownership != (domain, domain + "_owner"):
        raise ValueError("wrong domain database or ownership")
    if connection.execute(
        "SELECT has_database_privilege(current_user,current_database(),'CREATE'),has_database_privilege(current_user,current_database(),'TEMP'),has_schema_privilege(current_user,'public','CREATE'),has_schema_privilege(current_user,'public','USAGE'),has_schema_privilege(current_user,'domain','CREATE')"
    ).fetchone() != (False, False, False, False, False):
        raise ValueError("unexpected database or schema privilege")


def prepare_remediation_learning(fixture, schema):
    """Apply the existing remediation native-test setup to its owned Learning target."""
    with fixture.admin("learning") as database:
        database.execute("SET statement_timeout=5000")
        database.execute("SET ROLE learning_owner")
        database.execute("SET search_path=domain,pg_catalog")
        database.execute(schema.decode())
        database.execute("CREATE TABLE remediation_test_schedule(workspace_id text,card_id text,fsrs_json text NOT NULL,history_json text NOT NULL,PRIMARY KEY(workspace_id,card_id));"
            "CREATE TABLE remediation_test_queue(workspace_id text,card_id text,position integer,protected boolean DEFAULT false,PRIMARY KEY(workspace_id,card_id));")


class BoundedFixture(OwnershipFixture):
    def connection(self, database, role, password):
        import psycopg
        return psycopg.connect(host="127.0.0.1", port=self.port, dbname=database,
            user=role, password=password, autocommit=True, connect_timeout=3,
            options="-c statement_timeout=5000 -c lock_timeout=1000")
