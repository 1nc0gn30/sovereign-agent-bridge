"""Re-export of claim_manager module for backwards compatibility."""

from sovereign_agent_bridge.claim_manager import (
    ClaimConflictError,
    ClaimLock,
    ClaimManager,
    ClaimStatus,
)

__all__ = [
    "ClaimManager",
    "ClaimLock",
    "ClaimStatus",
    "ClaimConflictError",
]
