"""Pure response policy and retained explicit legacy unavailable boundary."""
from tempo_game_review_contracts import ComparisonReport, Unavailable
from .policy import FindingKind, classify_response
from .mapping import resolve_equivalent_mapping


class UnimplementedClassification:
    def classify(self, report: ComparisonReport) -> Unavailable:
        return Unavailable(component="classification", reason="classification is not implemented in the ingestion/comparison milestone")

__all__ = ["UnimplementedClassification", "FindingKind", "classify_response", "resolve_equivalent_mapping"]
