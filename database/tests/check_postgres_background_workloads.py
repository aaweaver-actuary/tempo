"""Run retained graph cleanup, restart and history proof on disposable PostgreSQL."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'database'), str(root / 'backend')]


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Background proof requires the disposable test runner')
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    from database.tests.check_postgres_graph_retention import run
    run()


if __name__ == '__main__':
    main()
