class IikoError(Exception):
    """Base integration error safe to catch in business services."""


class IikoUnavailableError(IikoError):
    pass


class IikoAuthenticationError(IikoError):
    pass


class IikoRequestError(IikoError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        correlation_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.correlation_id = correlation_id
