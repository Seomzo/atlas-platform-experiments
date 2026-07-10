"""Typed managed-worker failures with safe public messages."""


class AltasWorkerError(RuntimeError):
    """Base class for expected worker failures."""


class ControlPlaneUnavailable(AltasWorkerError):
    """The control plane could not be reached or returned an invalid response."""


class DeviceAuthenticationError(AltasWorkerError):
    """The device credential was rejected."""


class PolicyDenied(AltasWorkerError):
    """The requested managed action was denied."""

    def __init__(
        self, reason_code: str, message: str = "Action is not authorized"
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
