"""Public independent Intelligence composition and configuration."""

from .config import Configuration

__version__ = "0.1.0"

from . import analysis_v1

def run_analysis_work(configuration,run_id,maximum=1,client=None):
    """Public installed composition API; an external dispatcher may supply B's client.

    Intelligence owns startup, transactions and accepted results. The caller owns
    only its optional worker session; report serving has no worker dependency.
    """
    from tempo_intelligence_database.adapters.store import Store
    from tempo_intelligence_database.adapters.analysis_store import AnalysisStore
    from .analysis.worker import work
    store=None
    work_started=False
    try:
        store=Store(configuration)
        store.validate_startup()
        work_started=True
        return work(AnalysisStore(store),run_id,maximum,client=client)
    finally:
        if not work_started and client is not None:client.close()
        if store is not None:store.close()

__all__=["Configuration","create_app","analysis_v1","run_analysis_work"]


def __getattr__(name: str):
    """Load HTTP composition only when its supported export is requested."""
    if name == "create_app":
        from .api import create_app
        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
