"""Abstract Base Channel Adapter and data models for Sovereign Agent Bridge.

Defines the universal communication interface for privacy and sovereign messaging
channels (Signal, SimpleX, Telegram, Matrix, Webhooks), supporting synchronous and
asynchronous event dispatch, delivery verification, and health monitoring.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

logger = logging.getLogger("sovereign_agent_bridge.channels")


class ChannelType(str, Enum):
    """Enumeration of supported channel types."""

    SIGNAL = "signal"
    SIMPLEX = "simplex"
    TELEGRAM = "telegram"
    MATRIX = "matrix"
    WEBHOOK = "webhook"
    CUSTOM = "custom"


@dataclass
class ChannelAttachment:
    """Representation of an attachment (image, voice note, file, document)."""

    name: str
    content_type: str = "application/octet-stream"
    data: Optional[bytes] = None
    data_base64: Optional[str] = None
    path: Optional[str] = None
    is_voice_note: bool = False
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if self.data is not None and not self.data_base64:
            self.data_base64 = base64.b64encode(self.data).decode("ascii")
            self.size_bytes = len(self.data)
        elif self.data_base64 is not None and self.data is None:
            try:
                self.data = base64.b64decode(self.data_base64)
                self.size_bytes = len(self.data)
            except Exception:
                pass
        elif self.path and not self.data and not self.data_base64:
            p = Path(self.path)
            if p.is_file():
                self.size_bytes = p.stat().st_size

    def get_bytes(self) -> bytes:
        """Retrieve the raw binary content of the attachment."""
        if self.data is not None:
            return self.data
        if self.data_base64:
            return base64.b64decode(self.data_base64)
        if self.path:
            return Path(self.path).read_bytes()
        return b""

    def get_base64(self) -> str:
        """Retrieve base64-encoded content."""
        if self.data_base64:
            return self.data_base64
        return base64.b64encode(self.get_bytes()).decode("ascii")

    def to_dict(self) -> dict[str, Any]:
        """Convert attachment to JSON-serializable dictionary."""
        return {
            "name": self.name,
            "content_type": self.content_type,
            "data_base64": self.get_base64() if (self.data or self.data_base64) else None,
            "path": self.path,
            "is_voice_note": self.is_voice_note,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelAttachment:
        """Construct attachment from dictionary."""
        return cls(
            name=data.get("name", "attachment"),
            content_type=data.get("content_type", "application/octet-stream"),
            data_base64=data.get("data_base64"),
            path=data.get("path"),
            is_voice_note=bool(data.get("is_voice_note", False)),
            size_bytes=int(data.get("size_bytes", 0)),
        )


@dataclass
class ChannelMessage:
    """Universal standard message format exchanged across all channels."""

    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    channel_name: str = ""
    channel_type: str = ChannelType.CUSTOM.value
    sender_id: str = ""
    recipient_id: str = ""
    content: str = ""
    attachments: List[ChannelAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    is_group: bool = False
    group_id: Optional[str] = None
    reply_to_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert channel message to a dictionary representation."""
        return {
            "message_id": self.message_id,
            "channel_name": self.channel_name,
            "channel_type": self.channel_type,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "content": self.content,
            "attachments": [att.to_dict() for att in self.attachments],
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "raw_payload": self.raw_payload,
            "is_group": self.is_group,
            "group_id": self.group_id,
            "reply_to_id": self.reply_to_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelMessage:
        """Construct a channel message from a dictionary."""
        raw_attachments = data.get("attachments", [])
        attachments = [
            ChannelAttachment.from_dict(att) if isinstance(att, dict) else att
            for att in raw_attachments
        ]
        return cls(
            message_id=data.get("message_id", uuid.uuid4().hex),
            channel_name=data.get("channel_name", ""),
            channel_type=data.get("channel_type", ChannelType.CUSTOM.value),
            sender_id=data.get("sender_id", ""),
            recipient_id=data.get("recipient_id", ""),
            content=data.get("content", ""),
            attachments=attachments,
            metadata=data.get("metadata", {}),
            timestamp=float(data.get("timestamp", time.time())),
            raw_payload=data.get("raw_payload", {}),
            is_group=bool(data.get("is_group", False)),
            group_id=data.get("group_id"),
            reply_to_id=data.get("reply_to_id"),
        )


@dataclass
class DeliveryReceipt:
    """Verification receipt returned after sending a channel message."""

    success: bool
    message_id: str
    recipient_id: str
    channel_name: str
    timestamp: float = field(default_factory=time.time)
    status: str = "delivered"  # delivered, queued, failed, pending
    error_message: Optional[str] = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert delivery receipt to a dictionary."""
        return asdict(self)


@dataclass
class ChannelStatus:
    """Health inspection and telemetry report for an active channel."""

    channel_name: str
    channel_type: str
    is_healthy: bool
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert channel status to a dictionary."""
        return asdict(self)


class BaseChannelAdapter(ABC):
    """Abstract Base Class defining the contract for all messaging channel adapters."""

    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.config: dict[str, Any] = config or {}
        self.is_running: bool = False
        self._message_callbacks: List[Callable[[ChannelMessage], None]] = []

    @property
    @abstractmethod
    def channel_type(self) -> ChannelType:
        """Return the channel type enum."""
        ...

    @abstractmethod
    def send_message(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """Send a message to a specific recipient or group via this channel.

        Args:
            recipient: Destination phone number, chat ID, matrix room, or endpoint.
            content: Text body of the message.
            attachments: Optional files, photos, or voice notes.
            metadata: Optional routing or format instructions.

        Returns:
            DeliveryReceipt indicating success/failure and response details.
        """
        ...

    @abstractmethod
    def receive_events(self, timeout: float = 0.0) -> List[ChannelMessage]:
        """Poll or retrieve pending incoming messages from the channel.

        Args:
            timeout: Maximum time in seconds to wait for new events.

        Returns:
            List of received ChannelMessage objects.
        """
        ...

    @abstractmethod
    def health_check(self) -> ChannelStatus:
        """Inspect the connectivity and operational health of the channel adapter.

        Returns:
            ChannelStatus with latency and diagnostic details.
        """
        ...

    @abstractmethod
    def format_payload(
        self, content: str, metadata: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Format content and metadata into the channel's native wire payload.

        Args:
            content: Raw message text.
            metadata: Additional channel parameters.

        Returns:
            Dictionary matching the target service's API contract.
        """
        ...

    def start(self) -> None:
        """Start background polling, workers, or listeners for this channel."""
        self.is_running = True
        logger.info(f"Channel adapter '{self.name}' ({self.channel_type.value}) started.")

    def stop(self) -> None:
        """Gracefully terminate adapter listeners and release resources."""
        self.is_running = False
        logger.info(f"Channel adapter '{self.name}' ({self.channel_type.value}) stopped.")

    def on_message(self, callback: Callable[[ChannelMessage], None]) -> None:
        """Register a callback for incoming messages."""
        self._message_callbacks.append(callback)

    def _dispatch_message(self, message: ChannelMessage) -> None:
        """Deliver a received message to all registered listeners."""
        for cb in self._message_callbacks:
            try:
                cb(message)
            except Exception as e:
                logger.error(
                    f"Error in on_message callback for channel '{self.name}': {e}",
                    exc_info=True,
                )

    def validate_config(self, required_keys: Sequence[str]) -> None:
        """Ensure all required configuration parameters are present.

        Args:
            required_keys: Keys that must be non-empty in self.config.

        Raises:
            ValueError: If any required key is missing or empty.
        """
        missing = [k for k in required_keys if not self.config.get(k)]
        if missing:
            raise ValueError(
                f"Missing required configuration keys for channel '{self.name}': {', '.join(missing)}"
            )

    @staticmethod
    def http_request(
        url: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Perform a standard HTTP/HTTPS request using urllib.

        Args:
            url: Target URL.
            method: HTTP method (GET, POST, PUT, DELETE).
            data: Raw request body bytes.
            headers: HTTP header dictionary.
            timeout: Request timeout in seconds.

        Returns:
            Tuple of (status_code, response_bytes, response_headers).

        Raises:
            urllib.error.URLError: If connection fails.
        """
        req_headers = headers or {}
        req = urllib.request.Request(
            url=url,
            data=data,
            headers=req_headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status_code = response.status
                resp_bytes = response.read()
                resp_headers = {k.lower(): v for k, v in response.headers.items()}
                return status_code, resp_bytes, resp_headers
        except urllib.error.HTTPError as e:
            err_bytes = e.read()
            err_headers = {k.lower(): v for k, v in e.headers.items()}
            return e.code, err_bytes, err_headers
