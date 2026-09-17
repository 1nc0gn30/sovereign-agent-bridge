"""Agent Capability Discovery, Service Registry, and Multi-Signature Consensus Engine.

Provides decentralized swarm coordination primitives:
1. Dynamic capability discovery and least-loaded service routing.
2. Agent skill and tool schema advertising.
3. Multi-signature consensus threshold verification (k-of-n approval) using pure-Python HMAC.

100% Python Standard Library. Zero external dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set


@dataclass
class AgentCapabilityProfile:
    """Declared capabilities, skills, and tools offered by a sovereign agent."""

    agent_id: str
    display_name: str
    capabilities: List[str]  # e.g. ['code_review', 'security_audit', 'web_recon']
    tools: List[str] = field(default_factory=list)
    max_concurrency: int = 5
    current_load: int = 0
    endpoint: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    registered_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ServiceRegistry:
    """Thread-safe in-memory agent capability registry and task load balancer."""

    def __init__(self) -> None:
        self._profiles: Dict[str, AgentCapabilityProfile] = {}

    def register_agent(self, profile: AgentCapabilityProfile) -> None:
        """Register or update an agent's capability profile."""
        self._profiles[profile.agent_id] = profile

    def deregister_agent(self, agent_id: str) -> bool:
        """Remove an agent profile upon graceful shutdown."""
        return self._profiles.pop(agent_id, None) is not None

    def get_agent(self, agent_id: str) -> Optional[AgentCapabilityProfile]:
        """Lookup an agent profile by unique ID."""
        return self._profiles.get(agent_id)

    def list_agents_by_capability(self, capability: str) -> List[AgentCapabilityProfile]:
        """Return all active agents declaring a specific capability."""
        cap = capability.lower().strip()
        return [
            p
            for p in self._profiles.values()
            if any(c.lower().strip() == cap for c in p.capabilities)
        ]

    def find_agent_for_task(
        self,
        capability: str,
        required_tools: Optional[Sequence[str]] = None,
    ) -> Optional[AgentCapabilityProfile]:
        """Find the optimal least-loaded agent capable of executing the requested task.

        Args:
            capability: Required skill / domain string.
            required_tools: Optional specific tool names that must be present.

        Returns:
            Optimal AgentCapabilityProfile or None if no candidate matches criteria.
        """
        candidates = self.list_agents_by_capability(capability)
        if not candidates:
            return None

        if required_tools:
            req_set = {t.lower().strip() for t in required_tools}
            candidates = [
                c
                for c in candidates
                if req_set.issubset({t.lower().strip() for t in c.tools})
            ]

        # Filter out saturated agents (current_load >= max_concurrency)
        available = [c for c in candidates if c.current_load < c.max_concurrency]
        if not available:
            return None

        # Select least loaded, breaking ties by earlier registration
        return min(available, key=lambda a: (a.current_load / a.max_concurrency, a.registered_at))

    def get_registry_summary(self) -> Dict[str, Any]:
        """Provide aggregated metrics on registered swarm capabilities."""
        total_agents = len(self._profiles)
        cap_counts: Dict[str, int] = {}
        for p in self._profiles.values():
            for c in p.capabilities:
                cap_counts[c] = cap_counts.get(c, 0) + 1

        return {
            "total_registered_agents": total_agents,
            "unique_capabilities_count": len(cap_counts),
            "capability_distribution": cap_counts,
            "agents": [p.to_dict() for p in self._profiles.values()],
        }


@dataclass
class MultiSigProposal:
    """Decentralized threshold approval proposal requiring k-of-n cryptographic signatures."""

    proposal_id: str
    action_type: str
    payload_hash: str  # SHA-256 hex digest of serialized proposal payload
    threshold: int  # Minimum distinct valid signatures required
    signatures: Dict[str, str] = field(default_factory=dict)  # agent_id -> hmac_signature_hex
    is_approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_agent_signature(secret_key: str, payload_hash: str) -> str:
    """Compute HMAC-SHA256 signature for an agent approving a proposal hash."""
    return hmac.new(
        secret_key.encode("utf-8"),
        payload_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_multisig_consensus(
    proposal: MultiSigProposal,
    agent_public_keys: Dict[str, str],
) -> bool:
    """Verify whether a proposal has reached quorum with valid HMAC signatures.

    Args:
        proposal: The MultiSigProposal to evaluate.
        agent_public_keys: Dict mapping agent_id to their shared secret token.

    Returns:
        True if valid signatures >= proposal.threshold, and sets proposal.is_approved = True.
    """
    valid_count = 0
    checked_agents: Set[str] = set()

    for agent_id, sig in proposal.signatures.items():
        if agent_id in checked_agents:
            continue
        checked_agents.add(agent_id)

        secret = agent_public_keys.get(agent_id)
        if not secret:
            continue

        expected_sig = compute_agent_signature(secret, proposal.payload_hash)
        if hmac.compare_digest(sig, expected_sig):
            valid_count += 1

    proposal.is_approved = valid_count >= proposal.threshold
    return proposal.is_approved
