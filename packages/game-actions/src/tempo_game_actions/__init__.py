"""Explicit unavailable boundary; no classification or effects implemented."""
from tempo_game_review_contracts import ActionRequest, Unavailable


class UnimplementedActions:
    def execute(self, request: ActionRequest) -> Unavailable:
        return Unavailable(component="actions", reason="actions is not implemented in the ingestion/comparison milestone")

__all__ = ["UnimplementedActions"]
