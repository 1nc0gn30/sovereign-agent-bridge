"""Project Claim Lock System for Sovereign Agent Bridge.

Prevents conflicting agent file/resource writes through exclusive claims, cooperative
handoffs, time-to-live (TTL) expiration, conflict auditing, and persistent atomic lock
registries.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence, Union

from sovereign_agent_bridge.compat import (
    FileLock,
    atomic_write_text,
    ensure_dir,
    normalize_path,
    safe_read_text,
)

logger = logging.getLogger("sovereign_agent_bridge.claims")


class ClaimStatus(str, Enum):
    """Lifecycle status of a resource claim."""

    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    TRANSFERRED = "TRANSFERRED"


class ClaimConflictError(Exception):
    """Raised when an agent attempts to claim a resource already held by another agent."""

    def __init__(self, resource_path: str, holder_id: str, expires_at: float) -> None:
        self.resource_path = resource_path
        self.holder_id = holder_id
        self.expires_at = expires_at
        remaining = max(0.0, expires_at - time.time())
        super().__init__(
            f"Resource '{resource_path}' is already claimed by agent '{holder_id}' (expires in {remaining:.1f}s)"
        )


@dataclass
class ClaimLock:
    """Represents an active or historic resource claim lock."""

    claim_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    resource_path: str = ""
    holder_agent_id: str = ""
    acquired_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300.0)
    ttl_seconds: float = 300.0
    status: ClaimStatus = ClaimStatus.ACTIVE
    purpose: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    handoff_history: List[dict[str, Any]] = field(default_factory=list)

    def is_active(self) -> bool:
        """Check if the claim is currently active and unexpired."""
        return self.status == ClaimStatus.ACTIVE and time.time() < self.expires_at

    def remaining_seconds(self) -> float:
        """Return remaining seconds until claim expiration."""
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        """Convert claim lock to dictionary."""
        return {
            "claim_id": self.claim_id,
            "resource_path": self.resource_path,
            "holder_agent_id": self.holder_agent_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "ttl_seconds": self.ttl_seconds,
            "status": self.status.value,
            "purpose": self.purpose,
            "metadata": self.metadata,
            "handoff_history": self.handoff_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimLock:
        """Construct claim lock from dictionary."""
        status_val = data.get("status", "ACTIVE")
        return cls(
            claim_id=data.get("claim_id", uuid.uuid4().hex),
            resource_path=data.get("resource_path", ""),
            holder_agent_id=data.get("holder_agent_id", ""),
            acquired_at=float(data.get("acquired_at", time.time())),
            expires_at=float(data.get("expires_at", time.time() + 300.0)),
            ttl_seconds=float(data.get("ttl_seconds", 300.0)),
            status=ClaimStatus(status_val) if isinstance(status_val, str) else ClaimStatus.ACTIVE,
            purpose=data.get("purpose", ""),
            metadata=data.get("metadata", {}),
            handoff_history=list(data.get("handoff_history", [])),
        )


class ClaimManager:
    """Project Claim Lock Manager preventing concurrent agent write collisions."""

    def __init__(self, persistence_file: Optional[Union[str, Path]] = None) -> None:
        self.persistence_path: Optional[Path] = (
            normalize_path(persistence_file) if persistence_file else None
        )
        self._lock = threading.RLock()
        self._claims: Dict[str, ClaimLock] = {}  # normalized_resource_path -> ClaimLock
        self._load_persisted_claims()

    def _get_norm_key(self, resource_path: Union[str, Path]) -> str:
        """Normalize resource identifier or filesystem path into a stable string key."""
        try:
            return str(normalize_path(resource_path))
        except Exception:
            return str(resource_path).strip().replace("\\", "/")

    def _load_persisted_claims(self) -> None:
        """Load persistent claims from disk if configured."""
        if not self.persistence_path or not self.persistence_path.is_file():
            return

        try:
            content = safe_read_text(self.persistence_path)
            if not content.strip():
                return
            data = json.loads(content)
            for k, claim_dict in data.items():
                claim = ClaimLock.from_dict(claim_dict)
                # Only keep active claims or recent claims
                if claim.is_active():
                    self._claims[k] = claim
        except Exception as e:
            logger.warning(f"Could not load persisted claims from {self.persistence_path}: {e}")

    def _save_persisted_claims(self) -> None:
        """Atomically persist active claims to disk."""
        if not self.persistence_path:
            return

        data = {k: v.to_dict() for k, v in self._claims.items() if v.is_active()}
        raw = json.dumps(data, indent=2)
        try:
            atomic_write_text(self.persistence_path, raw)
        except Exception as e:
            logger.error(f"Failed to persist claims registry to {self.persistence_path}: {e}")

    def reap_expired(self) -> int:
        """Scan and transition expired claims to EXPIRED status."""
        reaped = 0
        now = time.time()
        with self._lock:
            for claim in self._claims.values():
                if claim.status == ClaimStatus.ACTIVE and claim.expires_at <= now:
                    claim.status = ClaimStatus.EXPIRED
                    reaped += 1
            if reaped > 0:
                self._save_persisted_claims()
        return reaped

    def claim(
        self,
        resource_path: Union[str, Path] = "",
        agent_id: str = "",
        ttl_seconds: float = 300.0,
        purpose: str = "",
        metadata: Optional[dict[str, Any]] = None,
        project_id: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> ClaimLock:
        """Acquire an exclusive claim lock on a resource or file.

        Args:
            resource_path: File path or resource identifier.
            agent_id: Identifier of the claiming agent spirit.
            ttl_seconds: Claim time-to-live in seconds (default: 300s).
            purpose: Brief description of intended changes.
            metadata: Additional context or tool metadata.

        Returns:
            The acquired ClaimLock object.

        Raises:
            ClaimConflictError: If claimed by another agent and not expired.
        """
        path_val = resource_path or project_id or ""
        eff_ttl = float(ttl) if ttl is not None else float(ttl_seconds)
        key = self._get_norm_key(path_val)
        now = time.time()

        with self._lock:
            self.reap_expired()
            existing = self._claims.get(key)

            if existing and existing.is_active():
                if existing.holder_agent_id == agent_id:
                    # Refresh / extend existing claim by the same agent
                    existing.expires_at = now + eff_ttl
                    existing.ttl_seconds = eff_ttl
                    if purpose:
                        existing.purpose = purpose
                    if metadata:
                        existing.metadata.update(metadata)
                    self._save_persisted_claims()
                    logger.info(f"Agent '{agent_id}' refreshed claim on '{key}' for {eff_ttl}s")
                    return existing
                else:
                    # Conflict: Held by another agent
                    raise ClaimConflictError(
                        resource_path=key,
                        holder_id=existing.holder_agent_id,
                        expires_at=existing.expires_at,
                    )

            # Create fresh claim lock
            new_claim = ClaimLock(
                resource_path=key,
                holder_agent_id=agent_id,
                acquired_at=now,
                expires_at=now + eff_ttl,
                ttl_seconds=eff_ttl,
                status=ClaimStatus.ACTIVE,
                purpose=purpose,
                metadata=metadata or {},
            )
            self._claims[key] = new_claim
            self._save_persisted_claims()
            logger.info(f"Agent '{agent_id}' acquired claim on '{key}' for {eff_ttl}s (purpose: '{purpose}')")
            return new_claim

    def release(
        self,
        resource_path: Union[str, Path] = "",
        agent_id: str = "",
        force: bool = False,
        project_id: Optional[str] = None,
    ) -> bool:
        """Release a previously acquired claim on a resource.

        Args:
            resource_path: Target file path or resource.
            agent_id: Releasing agent's identifier.
            force: If True, releases regardless of holder.

        Returns:
            True if released, False if no active claim existed.
        """
        path_val = resource_path or project_id or ""
        key = self._get_norm_key(path_val)

        with self._lock:
            self.reap_expired()
            existing = self._claims.get(key)

            if not existing or not existing.is_active():
                return False

            if not force and existing.holder_agent_id != agent_id:
                raise PermissionError(
                    f"Agent '{agent_id}' cannot release claim held by '{existing.holder_agent_id}' without force=True"
                )

            existing.status = ClaimStatus.RELEASED
            self._save_persisted_claims()
            logger.info(f"Released claim on '{key}' by agent '{agent_id}' (force={force})")
            return True

    def handoff(
        self,
        resource_path: Union[str, Path] = "",
        from_agent_id: str = "",
        to_agent_id: str = "",
        purpose: str = "",
        ttl_seconds: float = 300.0,
        project_id: Optional[str] = None,
        from_agent: Optional[str] = None,
        to_agent: Optional[str] = None,
        handoff_to: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> ClaimLock:
        """Cooperatively transfer a resource claim from one agent spirit to another.

        Args:
            resource_path: Target resource path.
            from_agent_id: Current claim holder.
            to_agent_id: New recipient agent spirit.
            purpose: Updated purpose for the next agent.
            ttl_seconds: New TTL window.

        Returns:
            Updated ClaimLock instance.
        """
        path_val = resource_path or project_id or ""
        src = from_agent_id or from_agent or ""
        dst = to_agent_id or to_agent or handoff_to or ""
        eff_ttl = float(ttl) if ttl is not None else float(ttl_seconds)
        key = self._get_norm_key(path_val)
        now = time.time()

        with self._lock:
            self.reap_expired()
            existing = self._claims.get(key)

            if not existing or not existing.is_active():
                # If no active claim exists, create one directly for dst
                return self.claim(path_val, dst, eff_ttl, purpose)

            if existing.holder_agent_id != src:
                raise PermissionError(
                    f"Cannot handoff: claim on '{key}' is held by '{existing.holder_agent_id}', not '{src}'"
                )

            # Record transfer event in handoff history
            existing.handoff_history.append(
                {
                    "from_agent": src,
                    "to_agent": dst,
                    "timestamp": now,
                    "previous_purpose": existing.purpose,
                }
            )

            existing.holder_agent_id = dst
            existing.acquired_at = now
            existing.expires_at = now + eff_ttl
            existing.ttl_seconds = eff_ttl
            existing.status = ClaimStatus.ACTIVE
            if purpose:
                existing.purpose = purpose

            self._save_persisted_claims()
            logger.info(f"Handoff claim on '{key}' from '{src}' to '{dst}'")
            return existing

    def renew(
        self,
        resource_path: Union[str, Path] = "",
        agent_id: str = "",
        extension_seconds: float = 300.0,
        project_id: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> bool:
        """Extend the TTL on an active claim held by the specified agent."""
        path_val = resource_path or project_id or ""
        eff_ttl = float(ttl) if ttl is not None else float(extension_seconds)
        key = self._get_norm_key(path_val)

        with self._lock:
            self.reap_expired()
            existing = self._claims.get(key)

            if not existing or not existing.is_active():
                return False

            if existing.holder_agent_id != agent_id:
                return False

            existing.expires_at = time.time() + eff_ttl
            existing.ttl_seconds = eff_ttl
            self._save_persisted_claims()
            return True

    def get_claim(self, resource_path: Union[str, Path]) -> Optional[ClaimLock]:
        """Retrieve active claim for a given resource path."""
        key = self._get_norm_key(resource_path)
        with self._lock:
            self.reap_expired()
            claim = self._claims.get(key)
            return claim if claim and claim.is_active() else None

    def list_claims(
        self,
        active_only: bool = True,
        agent_id: Optional[str] = None,
    ) -> List[ClaimLock]:
        """List all claims, optionally filtered by status or agent ID."""
        with self._lock:
            self.reap_expired()
            results: List[ClaimLock] = []
            for claim in self._claims.values():
                if active_only and not claim.is_active():
                    continue
                if agent_id and claim.holder_agent_id != agent_id:
                    continue
                results.append(claim)
            return results

    def check_conflicts(
        self,
        resource_paths: Sequence[Union[str, Path]],
        agent_id: str,
    ) -> List[dict[str, Any]]:
        """Check a list of resources for potential conflicts with other agents.

        Returns a list of conflict dicts with details on any active claims held by others.
        """
        conflicts: List[dict[str, Any]] = []
        with self._lock:
            self.reap_expired()
            for path in resource_paths:
                key = self._get_norm_key(path)
                existing = self._claims.get(key)
                if existing and existing.is_active() and existing.holder_agent_id != agent_id:
                    conflicts.append(
                        {
                            "resource_path": key,
                            "holder_agent_id": existing.holder_agent_id,
                            "expires_at": existing.expires_at,
                            "remaining_seconds": existing.remaining_seconds(),
                            "purpose": existing.purpose,
                        }
                    )
        return conflicts

    @contextlib.contextmanager
    def hold_claim(
        self,
        resource_path: Union[str, Path],
        agent_id: str,
        ttl_seconds: float = 300.0,
        purpose: str = "",
    ) -> Generator[ClaimLock, None, None]:
        """Context manager to acquire a claim and guarantee release on exit."""
        claim_obj = self.claim(resource_path, agent_id, ttl_seconds, purpose)
        try:
            yield claim_obj
        finally:
            with contextlib.suppress(Exception):
                self.release(resource_path, agent_id)
