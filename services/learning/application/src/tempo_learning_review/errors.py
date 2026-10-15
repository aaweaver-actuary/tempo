"""Failures translated by legacy transport adapters, never by the application."""

class ReviewFailure(Exception):
    def __init__(self, status_code: int, message: str, *, code=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retryable = retryable

    def information(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class ReviewConflict(ReviewFailure):
    def __init__(self, code: str, message: str, *, retryable=False):
        super().__init__(409, message, code=code, retryable=retryable)
