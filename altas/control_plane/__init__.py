"""Altas control-plane API.

Run the local prototype with::

    uvicorn altas.control_plane:create_app --factory
"""

from .app import create_app
from .config import ControlPlaneSettings

__all__ = ["ControlPlaneSettings", "create_app"]
