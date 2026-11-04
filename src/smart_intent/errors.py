class DomainError(Exception):
    """A safe, user-facing error; never contains credentials or absolute source paths."""

    def __init__(self, code, message, *, status="blocked", retryable=False, fields=None, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.fields = fields or {}
        self.data = data or {}
