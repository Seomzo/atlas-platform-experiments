"""Atlas control-plane API.

Run the local prototype with::

    uvicorn altas.control_plane:create_app --factory
"""

from .app import create_app
from .config import ControlPlaneSettings
from .identity import IdentityVerifier, ProviderIdentity

__all__ = [
    "ControlPlaneSettings",
    "IdentityVerifier",
    "ProviderIdentity",
    "create_app",
]
