"""Expose the published pure owner directly; no scoring or dedup copy."""
from app.analysis.coverage.personal_evidence import importance_from_encounters

__all__ = ["importance_from_encounters"]
