class ServiceError(Exception):
    """An error with a safe message and an HTTP-like status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status
