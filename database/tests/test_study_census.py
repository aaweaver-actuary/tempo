"""Static safeguards for the metadata-only Study census query."""

from pathlib import Path
import re
import unittest


SQL_PATH = Path(__file__).parents[1] / "tooling" / "study_census.sql"


def uncomment(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


class StudyCensusSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SQL_PATH.read_text(encoding="utf-8")
        cls.sql = uncomment(cls.source)
        cls.normalized = re.sub(r"\s+", " ", cls.sql).strip()

    def test_read_only_bounded_transaction_and_rollback(self):
        statements = [part.strip() for part in self.sql.split(";") if part.strip()]
        self.assertEqual(statements[0].upper(), "BEGIN READ ONLY")
        self.assertRegex(
            self.normalized,
            r"SET LOCAL statement_timeout\s*=\s*'10s'",
        )
        self.assertRegex(
            self.normalized,
            r"SET LOCAL lock_timeout\s*=\s*'1s'",
        )
        self.assertEqual(statements[-1].upper(), "ROLLBACK")
        self.assertNotIn("COMMIT", self.normalized.upper())

    def test_expected_catalog_sections_are_present(self):
        expected = (
            "server, database, connected role, and active schema identity",
            "non-system schema names",
            "relation sizes and reltuples estimates",
            "index metadata and sizes",
            "declared foreign-key relationships",
            "dependent non-system views and materialized views",
            "non-internal triggers",
            "learning_migration_history_exists",
            "scan counters",
            "statistics_reset_at",
        )
        for section in expected:
            with self.subTest(section=section):
                self.assertIn(section.lower(), self.source.lower())

    def test_no_mutation_commands_or_data_bearing_sources(self):
        forbidden = re.compile(
            r"\b(?:CREATE|INSERT|UPDATE|DELETE|DROP|ALTER|GRANT|REVOKE|"
            r"TRUNCATE|ANALYZE|VACUUM|CALL|DO|EXECUTE|PREPARE|DECLARE|"
            r"COMMIT|SAVEPOINT|RELEASE|COPY)\b",
            re.IGNORECASE,
        )
        self.assertIsNone(forbidden.search(self.sql))

        # Catalogs, PostgreSQL statistics views, and CTEs are the only FROM/JOIN sources.
        allowed_sources = {
            "pg_catalog.pg_settings",
            "pg_catalog.pg_namespace",
            "pg_catalog.pg_class",
            "pg_catalog.pg_index",
            "pg_catalog.pg_constraint",
            "pg_catalog.pg_depend",
            "pg_catalog.pg_rewrite",
            "pg_catalog.pg_trigger",
            "pg_catalog.pg_stat_all_tables",
            "pg_catalog.pg_stat_database",
            "pg_catalog.pg_database",
        }
        refs = re.findall(
            r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_$.]*)",
            self.sql,
            flags=re.IGNORECASE,
        )
        for ref in refs:
            with self.subTest(source=ref):
                self.assertIn(ref.lower(), allowed_sources)

        self.assertNotRegex(self.sql, r"\bpg_stat_activity\b")
        self.assertNotRegex(self.sql, r"\b(?:card|review|pgn)_[a-z0-9_]*\b")
        self.assertNotRegex(self.sql, r"\bSELECT\s+\*\b")
        self.assertNotRegex(self.sql, r"\bCOUNT\s*\(")
        self.assertIn("c.reltuples >= 0 THEN c.reltuples ELSE NULL", self.normalized)
        self.assertIn("NOT tg.tgisinternal", self.normalized)


if __name__ == "__main__":
    unittest.main()
