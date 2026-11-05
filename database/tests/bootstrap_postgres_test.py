"""Create a disposable PostgreSQL product schema and least-privilege roles."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql

from database.tooling.apply_postgres_migrations import apply_migrations
from database.tooling.provision_postgres_roles import provision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-dsn", required=True)
    parser.add_argument("--reader-password-file", type=Path, required=True)
    parser.add_argument("--writer-password-file", type=Path, required=True)
    parser.add_argument("--defense-zero", action="store_true", help="Explicitly retain all shared Defense data")
    options = parser.parse_args()
    if options.defense_zero and os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Explicit zero bootstrap requires the disposable runner")
    apply_migrations(options.admin_dsn, defense_zero=options.defense_zero)
    with psycopg.connect(options.admin_dsn) as database:
        database_name = database.execute("SELECT current_database()").fetchone()[0]
        database.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
            sql.Identifier(database_name), sql.Literal("tempo-disposable-postgres-test")))
        database.execute("INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING")
        database.execute("INSERT INTO tactic_rotation(id) VALUES(1) ON CONFLICT(id) DO NOTHING")
    provision(
        options.admin_dsn,
        options.reader_password_file.read_text().strip(),
        options.writer_password_file.read_text().strip(),
    )


if __name__ == "__main__":
    main()
