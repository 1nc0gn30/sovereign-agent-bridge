"""Cross-Bridge Federation Gateway & Multi-Hop Swarm Mesh Router.

Enables sovereign agent bridges to peer across cluster boundaries, local networks,
and cloud environments with routing loop prevention, hop-count constraints,
and selective topic federation.

100% Python Standard Library. Zero external dependencies.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


def topic_matches(pattern: str, topic: str) -> bool:
    """Check if topic matches a pattern (supports exact, '*', 'foo.*', 'foo.#')."""
    if pattern in ("*", "#") or pattern == topic:
        return True
    if pattern.endswith(".#"):
        prefix = pattern[:-2]
        return topic == prefix or topic.startswith(prefix + ".")
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        if topic.startswith(prefix + "."):
            remainder = topic[len(prefix) + 1:]
            return "." not in remainder
    return False


@dataclass
class BridgePeer:
    """A registered peer bridge node in the federation mesh."""

    peer_id: str
    endpoint_url: str
    auth_token: str
    status: str = "ONLINE"  # ONLINE, DEGRADED, OFFLINE
    federated_topics: List[str] = field(default_factory=lambda: ["*"])
    max_hops: int = 3
    latency_ms: float = 0.0
    last_seen_ms: int = 0
    messages_routed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FederatedMessage:
    """A cross-bridge federated swarm message carrying routing metadata."""

    message_id: str
    origin_bridge_id: str
    topic: str
    payload: Any
    hop_count: int = 0
    max_hops: int = 3
    visited_bridges: List[str] = field(default_factory=list)
    timestamp_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FederatedMessage:
        return cls(
            message_id=data["message_id"],
            origin_bridge_id=data["origin_bridge_id"],
            topic=data["topic"],
            payload=data["payload"],
            hop_count=int(data.get("hop_count", 0)),
            max_hops=int(data.get("max_hops", 3)),
            visited_bridges=list(data.get("visited_bridges", [])),
            timestamp_ms=int(data.get("timestamp_ms", int(time.time() * 1000))),
        )


class FederationGateway:
    """Gateway orchestrating cross-bridge peering, message federation, and loop defense."""

    def __init__(self, bridge_id: str, local_router: Optional[Any] = None):
        self.bridge_id = bridge_id
        self.local_router = local_router
        self._peers: Dict[str, BridgePeer] = {}
        self._local_subscribers: Dict[str, List[Callable[[FederatedMessage], None]]] = {}
        self._seen_message_ids: Set[str] = set()

    def register_peer(
        self,
        peer_id: str,
        endpoint_url: str,
        auth_token: Optional[str] = None,
        federated_topics: Optional[List[str]] = None,
        max_hops: int = 3,
    ) -> BridgePeer:
        """Register or update a federation peer bridge."""
        peer = BridgePeer(
            peer_id=peer_id,
            endpoint_url=endpoint_url,
            auth_token=auth_token or secrets.token_hex(16),
            status="ONLINE",
            federated_topics=federated_topics or ["*"],
            max_hops=max_hops,
            last_seen_ms=int(time.time() * 1000),
            messages_routed=0,
        )
        self._peers[peer_id] = peer
        return peer

    def unregister_peer(self, peer_id: str) -> bool:
        """Remove a peer from the federation gateway."""
        if peer_id in self._peers:
            del self._peers[peer_id]
            return True
        return False

    def list_peers(self) -> List[BridgePeer]:
        """List all active federation peers."""
        return list(self._peers.values())

    def subscribe_local(self, topic: str, callback: Callable[[FederatedMessage], None]) -> None:
        """Subscribe local agent handler to federated messages matching topic."""
        if topic not in self._local_subscribers:
            self._local_subscribers[topic] = []
        self._local_subscribers[topic].append(callback)

    def route_outbound(
        self,
        topic: str,
        payload: Any,
        target_peers: Optional[List[str]] = None,
        max_hops: int = 3,
    ) -> Dict[str, Any]:
        """Package and route a local message outward across federated peers."""
        message_id = f"fed-{secrets.token_hex(8)}"
        msg = FederatedMessage(
            message_id=message_id,
            origin_bridge_id=self.bridge_id,
            topic=topic,
            payload=payload,
            hop_count=0,
            max_hops=max_hops,
            visited_bridges=[self.bridge_id],
            timestamp_ms=int(time.time() * 1000),
        )
        self._seen_message_ids.add(message_id)

        dispatched_peers: List[str] = []
        candidates = target_peers if target_peers else list(self._peers.keys())

        for pid in candidates:
            peer = self._peers.get(pid)
            if not peer or peer.status != "ONLINE":
                continue

            # Check topic matching
            if any(topic_matches(pat, topic) for pat in peer.federated_topics):
                peer.messages_routed += 1
                dispatched_peers.append(pid)

        return {
            "message_id": message_id,
            "dispatched_peers": dispatched_peers,
            "total_dispatched": len(dispatched_peers),
            "envelope": msg.to_dict(),
        }

    def receive_inbound(
        self,
        raw_msg: Union[FederatedMessage, Dict[str, Any]],
    ) -> Tuple[bool, str, Optional[FederatedMessage]]:
        """Process incoming federated message with loop detection and local dispatch."""
        if isinstance(raw_msg, dict):
            msg = FederatedMessage.from_dict(raw_msg)
        else:
            msg = raw_msg

        # 1. Deduplication check
        if msg.message_id in self._seen_message_ids:
            return (False, f"Duplicate message '{msg.message_id}' dropped", None)
        self._seen_message_ids.add(msg.message_id)

        # 2. Routing loop check
        if self.bridge_id in msg.visited_bridges:
            return (False, f"Routing loop detected: '{self.bridge_id}' already in visited path {msg.visited_bridges}", None)

        # 3. Hop count check
        if msg.hop_count >= msg.max_hops:
            return (False, f"TTL/Hop limit exceeded: {msg.hop_count} >= max {msg.max_hops}", None)

        # Update hop count and visited list
        msg.hop_count += 1
        msg.visited_bridges.append(self.bridge_id)

        # 4. Dispatch to local subscribers
        dispatched_count = 0
        for topic_pattern, callbacks in self._local_subscribers.items():
            if topic_matches(topic_pattern, msg.topic):
                for cb in callbacks:
                    try:
                        cb(msg)
                        dispatched_count += 1
                    except Exception:
                        pass

        # Also dispatch to local router if present
        if self.local_router and hasattr(self.local_router, "dispatch"):
            try:
                self.local_router.dispatch(msg.topic, msg.payload)
            except Exception:
                pass

        return (True, f"Accepted and routed through bridge '{self.bridge_id}' (hops: {msg.hop_count})", msg)

    def get_topology(self) -> Dict[str, Any]:
        """Export federation topological graph and metrics."""
        return {
            "bridge_id": self.bridge_id,
            "peer_count": len(self._peers),
            "peers": [p.to_dict() for p in self._peers.values()],
            "active_topics": list(self._local_subscribers.keys()),
            "total_unique_messages_processed": len(self._seen_message_ids),
        }

    def format_topology_markdown(self) -> str:
        """Render topology summary as GitHub-Flavored Markdown."""
        lines = [
            f"### Federation Mesh Topology: `{self.bridge_id}`\n",
            f"- **Connected Peers**: {len(self._peers)}",
            f"- **Total Messages Processed**: {len(self._seen_message_ids)}\n",
            "| Peer ID | Endpoint | Status | Subscribed Topics | Hops Max | Routed |",
            "|---|---|---|---|---|---|",
        ]
        for p in self._peers.values():
            topics = ", ".join(p.federated_topics)
            lines.append(f"| `{p.peer_id}` | `{p.endpoint_url}` | **{p.status}** | `{topics}` | {p.max_hops} | {p.messages_routed} |")
        return "\n".join(lines)
