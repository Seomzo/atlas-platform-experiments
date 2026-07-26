"""Development-only Atlas collaboration orchestration."""

from .models import CollaborationEvent, TaskContract

__all__ = ["CollaborationEvent", "TaskContract"]
__version__ = "0.1.0"
PROTOCOL_VERSION = "atlas.collab.protocol.v1"
