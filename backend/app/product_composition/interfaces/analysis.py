"""Explicit existing Analysis composition entry points; runtime roots only.

Domain components must not import composition. No resources start on import.
"""
from ..analysis import CoverageRequest, coverage_settings_digest, prepare_coverage

__all__ = ["CoverageRequest", "coverage_settings_digest", "prepare_coverage"]
