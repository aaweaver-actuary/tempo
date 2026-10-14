import pytest
import psycopg
from psycopg import sql
from .conftest import FIXTURE_MODULE

DOMAINS = FIXTURE_MODULE.DOMAINS


@pytest.mark.parametrize("domain", DOMAINS)
def test_domain_runtime_reads_and_writes_only_its_authorized_data(ownership, domain):
    with ownership.runtime(domain) as connection:
        FIXTURE_MODULE.assert_component_credential(connection, domain)
        identifier = connection.execute(
            "INSERT INTO domain.settings(setting_key,setting_value) VALUES ('contract-test','first') RETURNING id"
        ).fetchone()[0]
        connection.execute(
            "UPDATE domain.settings SET setting_value='second' WHERE id=%s",
            (identifier,),
        )
        assert connection.execute(
            "SELECT setting_value FROM domain.settings WHERE id=%s", (identifier,)
        ).fetchone() == ("second",)
        connection.execute("DELETE FROM domain.settings WHERE id=%s", (identifier,))
        assert connection.execute(
            "SELECT revision FROM domain.migration_history"
        ).fetchall() == [(1,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("INSERT INTO domain.migration_history VALUES (2)")


@pytest.mark.parametrize(
    "domain,other",
    [(domain, other) for domain in DOMAINS for other in DOMAINS if other != domain],
)
def test_cross_domain_database_connections_are_rejected(ownership, domain, other):
    with pytest.raises(
        psycopg.OperationalError, match="permission denied for database"
    ):
        ownership.runtime(domain, other)


@pytest.mark.parametrize("domain", DOMAINS)
def test_runtime_cannot_escalate_or_create_unauthorized_objects(ownership, domain):
    with ownership.runtime(domain) as connection:
        for statement in [
            "CREATE TABLE public.unauthorized(id int)",
            "CREATE TABLE domain.unauthorized(id int)",
            "CREATE SCHEMA unauthorized",
            "CREATE TEMP TABLE unauthorized(id int)",
            "CREATE ROLE unauthorized",
            sql.SQL("SET ROLE {}").format(sql.Identifier(domain + "_owner")),
            "SET ROLE postgres",
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


@pytest.mark.parametrize("domain", DOMAINS)
def test_default_public_grants_and_future_objects_preserve_domain_isolation(
    ownership, domain
):
    with ownership.admin(domain) as administration:
        administration.execute(
            sql.SQL("SET ROLE {}").format(sql.Identifier(domain + "_owner"))
        )
        administration.execute(
            "CREATE TABLE domain.future_records(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, value text)"
        )
        administration.execute(
            "CREATE FUNCTION domain.future_function() RETURNS int LANGUAGE SQL AS 'SELECT 1'"
        )
        administration.execute("RESET ROLE")
        for other in DOMAINS:
            if other != domain:
                assert administration.execute(
                    "SELECT has_schema_privilege(%s,'domain','USAGE'),has_table_privilege(%s,'domain.future_records','SELECT'),has_function_privilege(%s,'domain.future_function()','EXECUTE')",
                    (other + "_runtime", other + "_runtime", other + "_runtime"),
                ).fetchone() == (False, False, False)
        public_grants = administration.execute("""
            SELECT grantee FROM pg_database, LATERAL aclexplode(COALESCE(datacl,acldefault('d',datdba))) WHERE datname=current_database() AND grantee=0
            UNION ALL SELECT grantee FROM pg_namespace, LATERAL aclexplode(COALESCE(nspacl,acldefault('n',nspowner))) WHERE nspname IN ('public','domain') AND grantee=0
            UNION ALL SELECT grantee FROM pg_class, LATERAL aclexplode(COALESCE(relacl,acldefault('r',relowner))) WHERE oid='domain.future_records'::regclass AND grantee=0
            UNION ALL SELECT grantee FROM pg_proc, LATERAL aclexplode(COALESCE(proacl,acldefault('f',proowner))) WHERE oid='domain.future_function()'::regprocedure AND grantee=0
        """).fetchall()
        assert public_grants == []
    with ownership.runtime(domain) as connection:
        connection.execute(
            "INSERT INTO domain.future_records(value) VALUES ('authorized')"
        )
        assert connection.execute(
            "SELECT value FROM domain.future_records"
        ).fetchall() == [("authorized",)]


@pytest.mark.parametrize("domain", DOMAINS)
def test_component_credentials_reject_admin_and_injected_superuser(ownership, domain):
    with ownership.admin(domain) as administration:
        with pytest.raises(ValueError, match="isolated runtime"):
            FIXTURE_MODULE.assert_component_credential(administration, domain)
        administration.execute(
            sql.SQL("ALTER ROLE {} SUPERUSER").format(
                sql.Identifier(domain + "_runtime")
            )
        )
        try:
            with ownership.runtime(domain) as connection:
                with pytest.raises(ValueError, match="administrative"):
                    FIXTURE_MODULE.assert_component_credential(connection, domain)
        finally:
            administration.execute(
                sql.SQL("ALTER ROLE {} NOSUPERUSER").format(
                    sql.Identifier(domain + "_runtime")
                )
            )


@pytest.mark.parametrize("domain", DOMAINS)
def test_component_guard_rejects_unexpected_public_schema_grants(ownership, domain):
    with ownership.admin(domain) as administration:
        administration.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        try:
            with ownership.runtime(domain) as connection:
                with pytest.raises(ValueError, match="unexpected"):
                    FIXTURE_MODULE.assert_component_credential(connection, domain)
        finally:
            administration.execute("REVOKE USAGE ON SCHEMA public FROM PUBLIC")
