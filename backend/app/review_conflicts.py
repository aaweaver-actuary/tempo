"""Machine-readable review conflicts, including durable command transport."""

from fastapi import HTTPException


class ReviewConflict(HTTPException):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(409, message)
        self.code = code
        self.retryable = retryable

    def information(self) -> dict:
        return {"code": self.code, "message": self.detail, "retryable": self.retryable}

