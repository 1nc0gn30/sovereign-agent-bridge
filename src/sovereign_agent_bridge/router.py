"""Unified Swarm Message Router for Sovereign Agent Bridge.

Coordinates inter-agent messaging, channel ingress/egress, agent spirit affinity routing,
in-memory circular event ring buffers, persistent atomic JSONL journaling, and dead-letter queues.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from sovereign_agent_bridge.channels.base import (
    BaseChannelAdapter,
    ChannelAttachment,
    ChannelMessage,
    DeliveryReceipt,
)
from sovereign_agent_bridge.compat import atomic_write_text, ensure_dir, normalize_path

logger = logging.getLogger("sovereign_agent_bridge.router")


@dataclass
class SwarmMessage:
    """Unified message envelope dispatched through the Swarm Router."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source_agent_id: str = ""
    target_agent_id: str = "broadcast"  # Agent spirit ID, "broadcast", or "channel:<name>:<recipient>"
    topic: str = "general"
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 2  # 0: Critical, 1: High, 2: Normal, 3: Low
    timestamp: float = field(default_factory=time.time)
    correlation_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    sequence_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize message to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmMessage:
        """Construct message from dictionary."""
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            source_agent_id=data.get("source_agent_id", ""),
            target_agent_id=data.get("target_agent_id", "broadcast"),
            topic=data.get("topic", "general"),
            payload=data.get("payload", {}),
            priority=int(data.get("priority", 2)),
            timestamp=float(data.get("timestamp", time.time())),
            correlation_id=data.get("correlation_id"),
            tags=list(data.get("tags", [])),
            sequence_number=int(data.get("sequence_number", 0)),
        )

    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> SwarmMessage:
        """Deserialize message from JSON string."""
        return cls.from_dict(json.loads(raw))


class RingBuffer:
    """Thread-safe circular in-memory buffer for fast event replay and recent inspection."""

    def __init__(self, capacity: int = 10000) -> None:
        self.capacity = capacity
        self._buffer: deque[SwarmMessage] = deque(maxlen=capacity)
        self._lock = threading.RLock()

    def append(self, message: SwarmMessage) -> None:
        """Append message to circular buffer."""
        with self._lock:
            self._buffer.append(message)

    def get_recent(self, count: int = 50) -> List[SwarmMessage]:
        """Get the N most recent messages."""
        with self._lock:
            items = list(self._buffer)
            return items[-count:] if count > 0 else items

    def get_since_sequence(self, sequence_number: int, limit: int = 500) -> List[SwarmMessage]:
        """Get messages with sequence numbers greater than the provided sequence_number."""
        with self._lock:
            matched = [msg for msg in self._buffer if msg.sequence_number > sequence_number]
            return matched[:limit]

    def size(self) -> int:
        """Get current number of messages in buffer."""
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        """Clear all messages from the buffer."""
        with self._lock:
            self._buffer.clear()


class PersistentJournal:
    """Persistent append-only JSONL event journal with atomic replay capability."""

    def __init__(self, journal_path: str | Path) -> None:
        self.journal_path = normalize_path(journal_path)
        ensure_dir(self.journal_path.parent)
        self._lock = threading.RLock()

    def append(self, message: SwarmMessage) -> None:
        """Append a swarm message entry to the persistent JSONL file."""
        line = message.to_json() + "\n"
        with self._lock:
            try:
                with open(self.journal_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
            except OSError as e:
                logger.error(f"Failed to append to journal {self.journal_path}: {e}")

    def replay(self, from_sequence: int = 0, limit: int = 1000) -> List[SwarmMessage]:
        """Replay messages from disk starting after from_sequence."""
        if not self.journal_path.is_file():
            return []

        messages: List[SwarmMessage] = []
        with self._lock:
            try:
                with open(self.journal_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = SwarmMessage.from_json(line)
                            if msg.sequence_number > from_sequence:
                                messages.append(msg)
                                if len(messages) >= limit:
                                    break
                        except Exception:
                            continue
            except OSError as e:
                logger.error(f"Failed to replay from journal {self.journal_path}: {e}")

        return messages


@dataclass
class DeadLetterEntry:
    """Record of an undeliverable or failed message."""

    message: SwarmMessage
    error: str
    failed_at: float = field(default_factory=time.time)
    retry_count: int = 0


class SwarmRouter:
    """Unified Swarm Message Router coordinating multi-agent and channel communications."""

    def __init__(
        self,
        journal_path: Optional[str | Path] = None,
        ring_buffer_capacity: int = 10000,
        ring_capacity: Optional[int] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._sequence_counter: int = 0
        cap = ring_capacity if ring_capacity is not None else ring_buffer_capacity
        self.ring_buffer = RingBuffer(capacity=cap)
        self.journal: Optional[PersistentJournal] = (
            PersistentJournal(journal_path) if journal_path else None
        )

        # Registered channel adapters: channel_name -> BaseChannelAdapter
        self._channels: Dict[str, BaseChannelAdapter] = {}

        # Subscriptions: topic_pattern -> list of (agent_id, handler_callback)
        self._subscriptions: Dict[str, List[tuple[str, Optional[Callable[[SwarmMessage], None]]]]] = {}

        # Agent Mailboxes: agent_id -> deque of SwarmMessage
        self._mailboxes: Dict[str, deque[SwarmMessage]] = {}

        # Dead Letter Queue: list of DeadLetterEntry
        self._dlq: List[DeadLetterEntry] = []

    def register_channel(self, adapter: BaseChannelAdapter) -> None:
        """Register a channel adapter and hook its inbound message listener."""
        with self._lock:
            self._channels[adapter.name] = adapter
            adapter.on_message(self._handle_channel_ingress)
            logger.info(f"Router registered channel adapter '{adapter.name}'")

    def unregister_channel(self, channel_name: str) -> Optional[BaseChannelAdapter]:
        """Unregister a channel adapter and return it."""
        with self._lock:
            adapter = self._channels.pop(channel_name, None)
            if adapter:
                logger.info(f"Router unregistered channel adapter '{channel_name}'")
            return adapter

    def get_channel(self, channel_name: str) -> Optional[BaseChannelAdapter]:
        """Retrieve a registered channel adapter by name."""
        with self._lock:
            return self._channels.get(channel_name)

    def subscribe(
        self,
        agent_id_or_pattern: Optional[str] = None,
        topic_pattern: Optional[str] = None,
        handler: Optional[Callable[[SwarmMessage], None]] = None,
        callback: Optional[Callable[[SwarmMessage], None]] = None,
    ) -> str:
        """Subscribe an agent spirit or callback to topics matching a wildcard pattern."""
        cb = callback or handler
        if topic_pattern is None:
            if agent_id_or_pattern is not None:
                pattern = agent_id_or_pattern
                agent_id = f"sub_{uuid.uuid4().hex[:8]}"
            else:
                pattern = "*"
                agent_id = f"sub_{uuid.uuid4().hex[:8]}"
        else:
            pattern = topic_pattern
            agent_id = agent_id_or_pattern or f"sub_{uuid.uuid4().hex[:8]}"

        with self._lock:
            if pattern not in self._subscriptions:
                self._subscriptions[pattern] = []
            # Prevent duplicate registration
            self._subscriptions[pattern] = [
                sub for sub in self._subscriptions[pattern] if sub[0] != agent_id
            ]
            self._subscriptions[pattern].append((agent_id, cb))
            if agent_id not in self._mailboxes:
                self._mailboxes[agent_id] = deque(maxlen=1000)
            logger.debug(f"Agent '{agent_id}' subscribed to topic pattern '{pattern}'")
            return agent_id

    def unsubscribe(
        self,
        sub_id_or_agent_id: Optional[str] = None,
        topic_pattern: Optional[str] = None,
        agent_id: Optional[str] = None,
        sub_id: Optional[str] = None,
    ) -> bool:
        """Unsubscribe an agent from a topic pattern or all topic patterns."""
        target_id = sub_id_or_agent_id or agent_id or sub_id or ""
        removed = False
        with self._lock:
            if topic_pattern:
                if topic_pattern in self._subscriptions:
                    orig_len = len(self._subscriptions[topic_pattern])
                    self._subscriptions[topic_pattern] = [
                        sub for sub in self._subscriptions[topic_pattern] if sub[0] != target_id
                    ]
                    if len(self._subscriptions[topic_pattern]) < orig_len:
                        removed = True
            else:
                for pat in list(self._subscriptions.keys()):
                    orig_len = len(self._subscriptions[pat])
                    self._subscriptions[pat] = [
                        sub for sub in self._subscriptions[pat] if sub[0] != target_id
                    ]
                    if len(self._subscriptions[pat]) < orig_len:
                        removed = True
        return removed

    def dispatch(self, message: SwarmMessage) -> bool:
        """Dispatch a swarm message to target agents, subscribers, or channels.

        Args:
            message: SwarmMessage instance to route.

        Returns:
            True if delivered to at least one subscriber/channel, False otherwise.
        """
        with self._lock:
            self._sequence_counter += 1
            message.sequence_number = self._sequence_counter

            # Store in ring buffer and journal
            self.ring_buffer.append(message)
            if self.journal:
                self.journal.append(message)

        delivered = False

        # Route to outbound channel if target specifies a channel: "channel:<name>:<recipient>"
        if message.target_agent_id.startswith("channel:"):
            parts = message.target_agent_id.split(":", 2)
            if len(parts) >= 3:
                chan_name, recipient = parts[1], parts[2]
                return self._dispatch_to_channel(chan_name, recipient, message)

        # Route direct message to specific agent
        if message.target_agent_id not in ("broadcast", "*", ""):
            direct_delivered = self._deliver_to_agent(message.target_agent_id, message)
            if direct_delivered:
                delivered = True
        else:
            # Route topic-based subscribers for broadcast / wildcard messages
            delivered_topics = self._dispatch_topic_subscribers(message)
            if delivered_topics:
                delivered = True

        if not delivered and message.target_agent_id not in ("broadcast", "*"):
            with self._lock:
                self._dlq.append(
                    DeadLetterEntry(
                        message=message,
                        error=f"No route or subscriber found for target '{message.target_agent_id}' and topic '{message.topic}'",
                    )
                )
            logger.warning(
                f"Undeliverable message {message.id} sent to DLQ (target={message.target_agent_id}, topic={message.topic})"
            )

        return delivered

    def _deliver_to_agent(self, agent_id: str, message: SwarmMessage) -> bool:
        """Deliver message directly into an agent's mailbox and trigger its callback."""
        with self._lock:
            if agent_id not in self._mailboxes:
                self._mailboxes[agent_id] = deque(maxlen=1000)
            self._mailboxes[agent_id].append(message)

            # Find matching handlers
            handlers: List[Callable[[SwarmMessage], None]] = []
            for pattern, subs in self._subscriptions.items():
                if fnmatch.fnmatch(message.topic, pattern):
                    for sub_agent, h in subs:
                        if sub_agent == agent_id and h is not None:
                            handlers.append(h)

        for handler in handlers:
            try:
                handler(message)
            except Exception as e:
                logger.error(f"Error in handler callback for agent '{agent_id}': {e}", exc_info=True)

        return True

    def _dispatch_topic_subscribers(self, message: SwarmMessage) -> bool:
        """Deliver message to all subscribers matching the message topic pattern."""
        recipients_to_deliver: Dict[str, List[Callable[[SwarmMessage], None]]] = {}

        with self._lock:
            for pattern, subs in self._subscriptions.items():
                if fnmatch.fnmatch(message.topic, pattern):
                    for agent_id, handler in subs:
                        # Avoid echoing back to sender if direct
                        if agent_id == message.source_agent_id and message.target_agent_id != "broadcast":
                            continue
                        if agent_id not in recipients_to_deliver:
                            recipients_to_deliver[agent_id] = []
                        if handler is not None:
                            recipients_to_deliver[agent_id].append(handler)

            for agent_id in recipients_to_deliver:
                if agent_id not in self._mailboxes:
                    self._mailboxes[agent_id] = deque(maxlen=1000)
                self._mailboxes[agent_id].append(message)

        delivered = bool(recipients_to_deliver)
        for agent_id, handlers in recipients_to_deliver.items():
            for h in handlers:
                try:
                    h(message)
                except Exception as e:
                    logger.error(f"Error in subscriber callback for agent '{agent_id}': {e}", exc_info=True)

        return delivered

    def _dispatch_to_channel(self, channel_name: str, recipient: str, message: SwarmMessage) -> bool:
        """Dispatch swarm message out to an external channel adapter."""
        adapter = self.get_channel(channel_name)
        if not adapter:
            with self._lock:
                self._dlq.append(
                    DeadLetterEntry(
                        message=message,
                        error=f"Channel '{channel_name}' is not registered with Router",
                    )
                )
            return False

        content = str(message.payload.get("content") or message.payload.get("text") or message.topic)
        attachments = None
        if "attachments" in message.payload and isinstance(message.payload["attachments"], list):
            attachments = [
                ChannelAttachment.from_dict(att) if isinstance(att, dict) else att
                for att in message.payload["attachments"]
            ]

        receipt = adapter.send_message(
            recipient=recipient,
            content=content,
            attachments=attachments,
            metadata=message.payload.get("metadata", {}),
        )

        if not receipt.success:
            with self._lock:
                self._dlq.append(
                    DeadLetterEntry(
                        message=message,
                        error=f"Channel '{channel_name}' delivery error: {receipt.error_message}",
                    )
                )
            return False

        return True

    def _handle_channel_ingress(self, channel_msg: ChannelMessage) -> None:
        """Handle incoming message from any registered channel adapter and route to swarm."""
        swarm_msg = SwarmMessage(
            id=uuid.uuid4().hex,
            source_agent_id=f"channel:{channel_msg.channel_name}:{channel_msg.sender_id}",
            target_agent_id="broadcast",
            topic=f"channel.{channel_msg.channel_type}.message",
            payload={
                "content": channel_msg.content,
                "sender_id": channel_msg.sender_id,
                "recipient_id": channel_msg.recipient_id,
                "is_group": channel_msg.is_group,
                "group_id": channel_msg.group_id,
                "attachments": [att.to_dict() for att in channel_msg.attachments],
                "metadata": channel_msg.metadata,
                "raw_payload": channel_msg.raw_payload,
            },
            timestamp=channel_msg.timestamp,
            tags=[channel_msg.channel_type, "ingress", channel_msg.channel_name],
        )

        logger.info(
            f"Ingressed channel message from '{channel_msg.channel_name}' (sender={channel_msg.sender_id})"
        )
        self.dispatch(swarm_msg)

    def fetch_mailbox(
        self,
        agent_id: str,
        max_messages: int = 50,
        clear: bool = True,
    ) -> List[SwarmMessage]:
        """Fetch pending messages from an agent spirit's mailbox."""
        with self._lock:
            mailbox = self._mailboxes.get(agent_id)
            if not mailbox:
                return []

            if not clear:
                return list(mailbox)[:max_messages]

            fetched: List[SwarmMessage] = []
            while mailbox and len(fetched) < max_messages:
                fetched.append(mailbox.popleft())
            return fetched

    def get_dlq_entries(self) -> List[DeadLetterEntry]:
        """Get copy of current Dead Letter Queue entries."""
        with self._lock:
            return list(self._dlq)

    def retry_dlq(self) -> int:
        """Attempt redelivery of all messages in the Dead Letter Queue."""
        with self._lock:
            entries = list(self._dlq)
            self._dlq.clear()

        requeued_count = 0
        for entry in entries:
            entry.retry_count += 1
            if self.dispatch(entry.message):
                requeued_count += 1
            else:
                with self._lock:
                    self._dlq.append(entry)

        return requeued_count
