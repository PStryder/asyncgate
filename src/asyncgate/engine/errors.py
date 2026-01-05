"""AsyncGate engine errors."""


class AsyncGateError(Exception):
    """Base error for AsyncGate operations."""

    def __init__(self, message: str, code: str = "ASYNCGATE_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class TaskNotFound(AsyncGateError):
    """Task does not exist."""

    def __init__(self, task_id: str):
        super().__init__(f"Task not found: {task_id}", "TASK_NOT_FOUND")
        self.task_id = task_id


class InvalidStateTransition(AsyncGateError):
    """Invalid task state transition."""

    def __init__(self, current_status: str, requested_status: str):
        super().__init__(
            f"Invalid transition from {current_status} to {requested_status}",
            "INVALID_STATE_TRANSITION",
        )
        self.current_status = current_status
        self.requested_status = requested_status


class LeaseInvalidOrExpired(AsyncGateError):
    """Lease is invalid or has expired."""

    def __init__(self, task_id: str = "", lease_id: str = ""):
        super().__init__(
            f"Lease invalid or expired for task {task_id}",
            "LEASE_INVALID_OR_EXPIRED",
        )
        self.task_id = task_id
        self.lease_id = lease_id


class UnauthorizedError(AsyncGateError):
    """Operation not authorized."""

    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, "UNAUTHORIZED")


class QuotaExceededError(AsyncGateError):
    """Quota exceeded."""

    def __init__(self, quota_type: str, limit: int):
        super().__init__(
            f"{quota_type} quota exceeded (limit: {limit})",
            "QUOTA_EXCEEDED",
        )
        self.quota_type = quota_type
        self.limit = limit


class RateLimitExceededError(AsyncGateError):
    """Rate limit exceeded."""

    def __init__(self, limit: int, window: str, retry_after: int):
        super().__init__(
            f"Rate limit exceeded ({limit}/{window})",
            "RATE_LIMIT_EXCEEDED",
        )
        self.limit = limit
        self.window = window
        self.retry_after = retry_after
