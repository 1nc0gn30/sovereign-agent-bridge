"""
Sovereign Agent Bridge.

Self-healing zero-dependency multi-channel communication bridge, MCP server,
consensus engine, claim manager, and dead-man switch watchdog for sovereign AI agents.
Pure Python standard library (zero external runtime dependencies).
"""

from __future__ import annotations

import os
import sys
import logging
from typing import Dict, List, Optional, Any, Union

__version__ = "0.1.0"
__author__ = "Sovereign Agent Bridge Contributors"
__license__ = "MIT"
__description__ = (
    "Self-healing zero-dependency multi-channel communication bridge, MCP server, "
    "consensus engine, claim manager, and dead-man switch watchdog for sovereign AI agents."
)

logger = logging.getLogger("sovereign_agent_bridge")

# Import Watchdog
from .watchdog import (
    HeartbeatWatchdog,
    AgentPulse,
    AgentStatus,
    AlertSeverity,
    DeadManSwitchAlert,
    get_default_watchdog,
    record_pulse,
    check_watchdog_liveness,
)

# Import MCP Server
from .mcp_server import (
    MCPServer,
    run_mcp_server,
    FallbackClaimManager,
    FallbackConsensusEngine,
)

# Import ConsensusEngine
try:
    from .consensus import ConsensusEngine
except ImportError:
    ConsensusEngine = FallbackConsensusEngine

# Import ClaimManager
try:
    from .claim_manager import ClaimManager
except ImportError:
    try:
        from .claims import ClaimManager
    except ImportError:
        ClaimManager = FallbackClaimManager

# Import SwarmRouter
try:
    from .router import SwarmRouter, SwarmMessage
except ImportError:
    SwarmRouter = None
    SwarmMessage = None


# High-Level SwarmBridge Facade
class SwarmBridge:
    """
    Unified High-Level Interface for Sovereign Agent Swarm Bridge.
    
    Coordinates multi-channel ingress/egress, distributed claim locks,
    3-way dialectic consensus cycles, and agent liveness watchdog.
    """

    def __init__(
        self,
        router: Optional[Any] = None,
        claims: Optional[Any] = None,
        consensus: Optional[Any] = None,
        watchdog: Optional[HeartbeatWatchdog] = None,
    ):
        self.router = router or (SwarmRouter() if SwarmRouter else None)
        self.claims = claims or ClaimManager()
        self.consensus = consensus or ConsensusEngine()
        self.watchdog = watchdog or get_default_watchdog()
        self.mcp = MCPServer()

    def send(
        self,
        channel: str,
        message: str,
        recipient: Optional[str] = None,
        attachment: Optional[str] = None,
        agent_id: str = "agent-local",
        priority: str = "NORMAL",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Send a routed message to a specific sovereign channel."""
        res = self.mcp.execute_tool("bridge_send", {
            "channel": channel,
            "message": message,
            "recipient": recipient,
            "attachment": attachment,
            "agent_id": agent_id,
            "priority": priority,
            **kwargs,
        })
        import json
        return json.loads(res["content"][0]["text"])

    def broadcast(
        self,
        message: str,
        channels: Optional[List[str]] = None,
        priority: str = "NORMAL",
        agent_id: str = "agent-broadcaster",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Broadcast message simultaneously across sovereign channels."""
        res = self.mcp.execute_tool("bridge_broadcast", {
            "message": message,
            "channels": channels,
            "priority": priority,
            "agent_id": agent_id,
            **kwargs,
        })
        import json
        return json.loads(res["content"][0]["text"])

    def list_channels(self, test_health: bool = False) -> Dict[str, Any]:
        """Query status and latency of configured channels."""
        res = self.mcp.execute_tool("bridge_list_channels", {"test_health": test_health})
        import json
        return json.loads(res["content"][0]["text"])

    def claim_project(
        self,
        project_id: str,
        agent_id: str,
        action: str = "claim",
        ttl: float = 300.0,
        handoff_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Acquire, renew, or release project lock."""
        res = self.mcp.execute_tool("bridge_claim_project", {
            "project_id": project_id,
            "agent_id": agent_id,
            "action": action,
            "ttl": ttl,
            "handoff_to": handoff_to,
            "metadata": metadata,
        })
        import json
        return json.loads(res["content"][0]["text"])

    def run_consensus(
        self,
        proposal: str,
        proponent: str = "Proponent-Agent",
        skeptic: str = "Skeptic-Agent",
        arbitrator: str = "Arbitrator-Agent",
        mode: str = "dialectic",
        rounds: int = 3,
    ) -> Dict[str, Any]:
        """Run 3-way dialectic consensus cycle on a proposal."""
        res = self.mcp.execute_tool("bridge_consensus", {
            "proposal": proposal,
            "proponent": proponent,
            "skeptic": skeptic,
            "arbitrator": arbitrator,
            "mode": mode,
            "rounds": rounds,
        })
        import json
        return json.loads(res["content"][0]["text"])

    def pulse(
        self,
        agent_id: str,
        interval: float = 60.0,
        timeout: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentPulse:
        """Record heartbeat pulse for an agent."""
        return self.watchdog.record_pulse(
            agent_id=agent_id,
            interval=interval,
            timeout=timeout,
            metadata=metadata,
        )

    def stats(self, detailed: bool = False) -> Dict[str, Any]:
        """Return system telemetry and throughput metrics."""
        res = self.mcp.execute_tool("bridge_stats", {"detailed": detailed})
        import json
        return json.loads(res["content"][0]["text"])

    def diagnostics(self, verbose: bool = False) -> Dict[str, Any]:
        """Run system diagnostics."""
        res = self.mcp.execute_tool("bridge_diagnostics", {"verbose": verbose})
        import json
        return json.loads(res["content"][0]["text"])


# Top-level convenience functions
_default_bridge: Optional[SwarmBridge] = None

def get_default_bridge() -> SwarmBridge:
    """Retrieve or initialize default shared SwarmBridge instance."""
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = SwarmBridge()
    return _default_bridge


def send_bridge_message(
    channel: str,
    message: str,
    recipient: Optional[str] = None,
    attachment: Optional[str] = None,
    agent_id: str = "agent-local",
    priority: str = "NORMAL",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Send a routed message via the default SwarmBridge."""
    return get_default_bridge().send(
        channel=channel,
        message=message,
        recipient=recipient,
        attachment=attachment,
        agent_id=agent_id,
        priority=priority,
        **kwargs,
    )


def broadcast_message(
    message: str,
    channels: Optional[List[str]] = None,
    priority: str = "NORMAL",
    agent_id: str = "agent-broadcaster",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Broadcast a message across sovereign channels via the default SwarmBridge."""
    return get_default_bridge().broadcast(
        message=message,
        channels=channels,
        priority=priority,
        agent_id=agent_id,
        **kwargs,
    )


try:
    from .service_discovery import (
        AgentCapabilityProfile,
        ServiceRegistry,
        MultiSigProposal,
        compute_agent_signature,
        verify_multisig_consensus,
    )
except ImportError:
    pass

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "__description__",
    "SwarmBridge",
    "SwarmRouter",
    "ConsensusEngine",
    "ClaimManager",
    "HeartbeatWatchdog",
    "send_bridge_message",
    "broadcast_message",
    "AgentPulse",
    "AgentStatus",
    "AlertSeverity",
    "DeadManSwitchAlert",
    "MCPServer",
    "run_mcp_server",
    "get_default_watchdog",
    "record_pulse",
    "check_watchdog_liveness",
    "get_default_bridge",
    "AgentCapabilityProfile",
    "ServiceRegistry",
    "MultiSigProposal",
    "compute_agent_signature",
    "verify_multisig_consensus",
]
