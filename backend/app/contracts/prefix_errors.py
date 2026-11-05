"""Public diagnostic exception shared by HTTP and command admission."""

from fastapi import HTTPException


class PrefixEvaluationForegroundDeferred(HTTPException):
    """Keep the public retry response distinct from an execution deadline."""

    def __init__(self):
        super().__init__(
            503,
            {
                "code": "evaluation_busy",
                "message": "Study work is active. Retry the diagnostic when study is idle.",
            },
            headers={"Retry-After": "1"},
        )
