"""Learning runtime public capabilities; importing opens no infrastructure."""

from . import scheduling_review_application
from . import scheduling_review_contracts
from . import scheduling_review_store
from . import study_grading
from . import opening_decision_evidence
from . import threat_models
from . import threat_validation
from . import threat_grading
from . import queue_ordering
from . import review_reconciliation
from . import study_contracts
from . import opening_evidence_contracts

__all__ = [
    "scheduling_review_application",
    "scheduling_review_contracts",
    "scheduling_review_store",
    "study_grading",
    "opening_decision_evidence",
    "threat_models",
    "threat_validation",
    "threat_grading",
    "queue_ordering",
    "review_reconciliation",
    "study_contracts",
    "opening_evidence_contracts",
    "Configuration",
    "create_app",
    "register_evidence_receiver",
    "register_settlement",
]

from .config import Configuration


def register_evidence_receiver(handler):
    from .receivers import register_evidence_receiver as register

    return register(handler)


def register_settlement(handler):
    from .receivers import register_settlement as register

    return register(handler)


def create_app(configuration):
    from .api import create_app as compose

    return compose(configuration)
