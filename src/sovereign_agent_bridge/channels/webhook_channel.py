"""Generic HMAC-SHA256 Signed Webhook Channel Adapter for Sovereign Agent Bridge.

Dispatches cryptographic HMAC-SHA256 signed JSON webhook payloads to remote HTTP/HTTPS
endpoints with exponential backoff retries, timestamp validation, and inbound signature
verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, List, Optional, Sequence

from sovereign_agent_bridge.channels.base import (
    BaseChannelAdapter,
    ChannelAttachment,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
    DeliveryReceipt,
)

logger = logging.getLogger("sovereign_agent_bridge.channels.webhook")


def compute_hmac_sha256(
    arg1: str | bytes = "",
    arg2: str | bytes = "",
    data: Optional[bytes | str] = None,
    secret: Optional[str | bytes] = None,
) -> str:
    """Compute HMAC-SHA256 hexadecimal digest for raw binary or string data.

    Accepts (secret, data) or (data, secret) or keyword arguments (secret=..., data=...).
    """
    if data is not None and secret is not None:
        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        payload = data.encode("utf-8") if isinstance(data, str) else data
    elif isinstance(arg1, bytes) and isinstance(arg2, str):
        # arg1 is payload, arg2 is secret
        key = arg2.encode("utf-8")
        payload = arg1
    elif isinstance(arg1, str) and isinstance(arg2, bytes):
        # arg1 is secret, arg2 is payload
        key = arg1.encode("utf-8")
        payload = arg2
    elif isinstance(arg1, bytes) and isinstance(arg2, bytes):
        key = arg2
        payload = arg1
    else:
        key = str(arg1).encode("utf-8")
        payload = str(arg2).encode("utf-8")

    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    payload_bytes: bytes,
    signature_header: str,
    secret: str | bytes,
    timestamp_header: Optional[str] = None,
    max_age_seconds: float = 300.0,
) -> bool:
    """Verify an incoming HMAC-SHA256 webhook signature.

    Supports signatures in formats:
        - "sha256=<hex>"
        - "<hex>"

    If timestamp_header is provided, enforces maximum message age to prevent replay attacks.

    Args:
        payload_bytes: Raw request body bytes.
        signature_header: Value from X-Signature-256 or X-Hub-Signature-256 header.
        secret: Shared webhook secret key.
        timestamp_header: Optional epoch timestamp header (X-Signature-Timestamp).
        max_age_seconds: Maximum permitted age of message in seconds.

    Returns:
        True if signature is authentic and timestamp is valid, False otherwise.
    """
    if not signature_header or not secret:
        return False

    # Check timestamp freshness if present
    if timestamp_header:
        try:
            req_ts = float(timestamp_header)
            if abs(time.time() - req_ts) > max_age_seconds:
                logger.warning(f"Webhook signature expired (drift: {abs(time.time() - req_ts):.1f}s)")
                return False
        except (ValueError, TypeError):
            return False

    sig_to_check = signature_header.strip()
    if sig_to_check.startswith("sha256="):
        sig_to_check = sig_to_check[len("sha256=") :]

    # Check direct body HMAC or (timestamp + "." + body) HMAC
    expected_direct = compute_hmac_sha256(secret, payload_bytes)
    if hmac.compare_digest(expected_direct.lower(), sig_to_check.lower()):
        return True

    if timestamp_header:
        composite = f"{timestamp_header}.".encode("utf-8") + payload_bytes
        expected_composite = compute_hmac_sha256(secret, composite)
        if hmac.compare_digest(expected_composite.lower(), sig_to_check.lower()):
            return True

    return False


class WebhookChannelAdapter(BaseChannelAdapter):
    """Channel adapter for HMAC-SHA256 signed HTTP webhooks."""

    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize Webhook channel adapter.

        Config keys:
            - target_url: Default destination URL for outgoing webhooks
            - secret_key: HMAC-SHA256 shared secret key for payload signing
            - max_retries: Maximum delivery retry attempts on network error (default: 3)
            - retry_backoff: Initial backoff delay in seconds (default: 0.5)
            - signature_header: Header name for signature (default: "X-Signature-256")
            - timestamp_header: Header name for timestamp (default: "X-Signature-Timestamp")
            - timeout: Request timeout in seconds (default: 15.0)
        """
        super().__init__(name, config)
        self.target_url: str = str(
            self.config.get("target_url") or self.config.get("url") or ""
        ).strip()
        self.secret_key: str = str(
            self.config.get("secret_key") or self.config.get("secret") or ""
        ).strip()
        self.max_retries: int = int(self.config.get("max_retries", 3))
        self.retry_backoff: float = float(self.config.get("retry_backoff", 0.5))
        self.sig_header_name: str = self.config.get("signature_header", "X-Signature-256")
        self.ts_header_name: str = self.config.get("timestamp_header", "X-Signature-Timestamp")
        self.timeout: float = float(self.config.get("timeout", 15.0))
        self._inbound_queue: List[ChannelMessage] = []

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.WEBHOOK

    def format_payload(
        self, content: str, metadata: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Format message into standard webhook JSON event payload."""
        meta = metadata or {}
        return {
            "event_id": meta.get("event_id", uuid.uuid4().hex),
            "timestamp": meta.get("timestamp", time.time()),
            "source_channel": self.name,
            "event_type": meta.get("event_type", "message.created"),
            "content": content,
            "metadata": meta,
        }

    def send_message(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """Dispatch a signed webhook HTTP POST request with retry backoff.

        Args:
            recipient: Target URL (overrides default target_url if provided).
            content: Message payload or JSON text.
            attachments: Optional attachments included in the webhook payload.
            metadata: Custom event metadata.
        """
        msg_id = uuid.uuid4().hex
        meta = dict(metadata or {})
        dest_url = recipient if recipient.startswith("http://") or recipient.startswith("https://") else self.target_url

        if not dest_url:
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id="",
                channel_name=self.name,
                status="failed",
                error_message="Target webhook URL is not specified.",
                raw_response={},
            )

        payload_dict = self.format_payload(content, meta)
        payload_dict["message_id"] = msg_id
        if attachments:
            payload_dict["attachments"] = [att.to_dict() for att in attachments]

        payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        current_ts = str(int(time.time()))
        nonce = uuid.uuid4().hex

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SovereignAgentBridge-Webhook/1.0",
            self.ts_header_name: current_ts,
            "X-Signature-Nonce": nonce,
        }

        if self.secret_key:
            # Composite signature over timestamp + body
            composite = f"{current_ts}.".encode("utf-8") + payload_bytes
            sig = compute_hmac_sha256(self.secret_key, composite)
            headers[self.sig_header_name] = f"sha256={sig}"

        last_error = None
        last_resp_data: dict[str, Any] = {}

        for attempt in range(self.max_retries):
            try:
                code, resp_bytes, _ = self.http_request(
                    url=dest_url,
                    method="POST",
                    data=payload_bytes,
                    headers=headers,
                    timeout=self.timeout,
                )

                try:
                    last_resp_data = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
                except Exception:
                    last_resp_data = {"raw": resp_bytes.decode("utf-8", errors="replace")}

                if 200 <= code < 300:
                    return DeliveryReceipt(
                        success=True,
                        message_id=msg_id,
                        recipient_id=dest_url,
                        channel_name=self.name,
                        status="delivered",
                        raw_response=last_resp_data,
                    )
                else:
                    last_error = f"HTTP status code {code}"
            except Exception as e:
                last_error = str(e)

            # Exponential backoff
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_backoff * (2**attempt))

        return DeliveryReceipt(
            success=False,
            message_id=msg_id,
            recipient_id=dest_url,
            channel_name=self.name,
            status="failed",
            error_message=f"Webhook dispatch failed after {self.max_retries} attempts: {last_error}",
            raw_response=last_resp_data,
        )

    def process_incoming_payload(
        self,
        raw_body: bytes,
        headers: dict[str, str],
        enforce_signature: bool = True,
    ) -> Optional[ChannelMessage]:
        """Ingest and verify an incoming webhook request.

        Args:
            raw_body: Raw request body in bytes.
            headers: HTTP request headers dictionary (case-insensitive keys).
            enforce_signature: If True, rejects requests with invalid HMAC signatures.

        Returns:
            Constructed ChannelMessage, or None if signature fails.
        """
        # Lowercase headers map
        norm_headers = {k.lower(): v for k, v in headers.items()}
        sig_header = norm_headers.get(self.sig_header_name.lower()) or norm_headers.get("x-hub-signature-256")
        ts_header = norm_headers.get(self.ts_header_name.lower())

        if enforce_signature and self.secret_key:
            if not sig_header or not verify_webhook_signature(
                payload_bytes=raw_body,
                signature_header=sig_header,
                secret=self.secret_key,
                timestamp_header=ts_header,
            ):
                logger.warning("Incoming webhook signature verification failed.")
                return None

        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except Exception:
            parsed = {"raw": raw_body.decode("utf-8", errors="replace")}

        content = parsed.get("content") or parsed.get("message") or parsed.get("text") or json.dumps(parsed)
        sender = parsed.get("sender_id") or parsed.get("source") or "webhook_sender"
        msg_id = parsed.get("message_id") or parsed.get("event_id") or uuid.uuid4().hex

        msg = ChannelMessage(
            message_id=msg_id,
            channel_name=self.name,
            channel_type=self.channel_type.value,
            sender_id=sender,
            recipient_id="self",
            content=str(content),
            metadata={"headers": headers, "webhook_event": parsed.get("event_type")},
            timestamp=float(parsed.get("timestamp", time.time())),
            raw_payload=parsed,
        )

        self._inbound_queue.append(msg)
        self._dispatch_message(msg)
        return msg

    def receive_events(self, timeout: float = 0.0) -> List[ChannelMessage]:
        """Retrieve events accumulated in the in-memory inbound queue."""
        events = list(self._inbound_queue)
        self._inbound_queue.clear()
        return events

    def health_check(self) -> ChannelStatus:
        """Inspect target webhook endpoint accessibility if configured."""
        start_time = time.monotonic()
        details = {
            "target_url": self.target_url,
            "signing_enabled": bool(self.secret_key),
        }

        if not self.target_url:
            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=True,
                latency_ms=0.0,
                details=details,
            )

        try:
            # Send HEAD or GET request to verify URL reachability
            code, _, _ = self.http_request(url=self.target_url, method="GET", timeout=5.0)
            latency = (time.monotonic() - start_time) * 1000.0
            # Even 404 or 405 means endpoint server is alive and reachable
            is_healthy = code < 500
            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=is_healthy,
                latency_ms=latency,
                details=details,
                error=f"Endpoint returned HTTP {code}" if not is_healthy else None,
            )
        except Exception as e:
            latency = (time.monotonic() - start_time) * 1000.0
            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=False,
                latency_ms=latency,
                details=details,
                error=str(e),
            )
