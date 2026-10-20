"""Explicit unavailable boundary; no classification or effects implemented."""
from tempo_game_review_contracts import ComparisonReport, Unavailable


class UnimplementedClassification:
    def classify(self, report: ComparisonReport) -> Unavailable:
        return Unavailable(component="classification", reason="classification is not implemented in the ingestion/comparison milestone")

__all__ = ["UnimplementedClassification"]
