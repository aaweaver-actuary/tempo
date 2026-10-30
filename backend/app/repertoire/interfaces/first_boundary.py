"""Exact-game immutable execution capability for first-boundary consumers.

Repertoire constructs the capability from complete owner facts. This facade
re-exports that same implementation and owns no startup, read or writer action.
"""
from ..feedback_scope import PreparedFirstBoundaryRepertoire

__all__ = ["PreparedFirstBoundaryRepertoire"]
