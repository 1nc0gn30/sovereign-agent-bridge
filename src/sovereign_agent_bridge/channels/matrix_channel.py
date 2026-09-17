"""Matrix Protocol Client-Server Channel Adapter for Sovereign Agent Bridge.

Provides Matrix messaging capabilities via the Matrix Client-Server REST API (v3/r0),
supporting room sync, transaction ID deduplication, markdown/HTML formatting,
media upload (mxc://), and E2EE room awareness.
"""

from __future__ import annotations

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

logger = logging.getLogger("sovereign_agent_bridge.channels.matrix")


class MatrixChannelAdapter(BaseChannelAdapter):
    """Channel adapter for Matrix federated chat network."""

    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the Matrix adapter.

        Config keys:
            - homeserver_url: Base URL of the Matrix homeserver (e.g. "https://matrix.org")
            - access_token: Matrix client access token
            - user_id: Matrix user ID (e.g. "@agent:matrix.org")
            - default_room_id: Default Matrix room ID (e.g. "!roomId:matrix.org")
            - device_id: Matrix device ID (Optional)
            - timeout: Request timeout in seconds (default: 30.0)
        """
        super().__init__(name, config)
        self.homeserver: str = str(self.config.get("homeserver_url", "https://matrix.org")).rstrip("/")
        self.access_token: str = str(self.config.get("access_token", "")).strip()
        self.user_id: str = str(self.config.get("user_id", "")).strip()
        self.default_room_id: Optional[str] = self.config.get("default_room_id")
        self.device_id: Optional[str] = self.config.get("device_id")
        self.timeout: float = float(self.config.get("timeout", 30.0))
        self._sync_token: Optional[str] = None
        self._joined_rooms: set[str] = set()

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.MATRIX

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        """Generate headers with Matrix Bearer token."""
        headers = {"Content-Type": content_type}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def format_payload(
        self, content: str, metadata: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Format Matrix m.room.message event content."""
        meta = metadata or {}
        msgtype = meta.get("msgtype", "m.text")
        payload: dict[str, Any] = {
            "msgtype": msgtype,
            "body": content,
        }

        # Support HTML formatted messages
        if meta.get("formatted_body"):
            payload["format"] = "org.matrix.custom.html"
            payload["formatted_body"] = meta["formatted_body"]

        return payload

    def send_message(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """Send an event or message to a Matrix room.

        Args:
            recipient: Room ID (e.g. "!xyz:matrix.org") or room alias.
            content: Message body.
            attachments: Optional attachments to upload and send.
            metadata: Additional metadata (msgtype, formatted_body, etc.).
        """
        msg_id = uuid.uuid4().hex
        meta = dict(metadata or {})
        room_id = recipient or meta.get("room_id") or self.default_room_id

        if not room_id:
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id="",
                channel_name=self.name,
                status="failed",
                error_message="Matrix room_id is required.",
                raw_response={},
            )

        if not self.access_token:
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id=room_id,
                channel_name=self.name,
                status="failed",
                error_message="Matrix access_token is not configured.",
                raw_response={},
            )

        try:
            # If attachments exist, upload media first
            if attachments:
                for att in attachments:
                    mxc_url = self._upload_media(att)
                    if mxc_url:
                        self._send_media_event(room_id, att, mxc_url)

            # Send main text message
            txn_id = f"m{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            encoded_room = urllib.parse.quote(room_id)
            url = f"{self.homeserver}/_matrix/client/v3/rooms/{encoded_room}/send/m.room.message/{txn_id}"

            payload = self.format_payload(content, meta)
            data_bytes = json.dumps(payload).encode("utf-8")

            code, resp_bytes, _ = self.http_request(
                url=url,
                method="PUT",
                data=data_bytes,
                headers=self._headers(),
                timeout=self.timeout,
            )

            try:
                resp_data = json.loads(resp_bytes.decode("utf-8"))
            except Exception:
                resp_data = {"raw": resp_bytes.decode("utf-8", errors="replace")}

            success = 200 <= code < 300
            event_id = resp_data.get("event_id", msg_id)

            return DeliveryReceipt(
                success=success,
                message_id=event_id,
                recipient_id=room_id,
                channel_name=self.name,
                status="delivered" if success else "failed",
                error_message=None if success else f"Matrix error {code}: {resp_data}",
                raw_response=resp_data,
            )
        except Exception as e:
            logger.error(f"Matrix send failed to '{room_id}': {e}", exc_info=True)
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id=room_id,
                channel_name=self.name,
                status="failed",
                error_message=str(e),
                raw_response={"error": str(e)},
            )

    def _upload_media(self, attachment: ChannelAttachment) -> Optional[str]:
        """Upload attachment to Matrix media repository (returns mxc:// URI)."""
        filename = urllib.parse.quote(attachment.name or "file")
        url = f"{self.homeserver}/_matrix/media/v3/upload?filename={filename}"

        headers = self._headers(content_type=attachment.content_type)
        file_bytes = attachment.get_bytes()

        code, resp_bytes, _ = self.http_request(
            url=url, method="POST", data=file_bytes, headers=headers, timeout=self.timeout
        )

        if 200 <= code < 300:
            try:
                data = json.loads(resp_bytes.decode("utf-8"))
                return data.get("content_uri")
            except Exception:
                pass
        return None

    def _send_media_event(self, room_id: str, attachment: ChannelAttachment, mxc_url: str) -> None:
        """Send uploaded media event to the room."""
        txn_id = f"m{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        encoded_room = urllib.parse.quote(room_id)
        url = f"{self.homeserver}/_matrix/client/v3/rooms/{encoded_room}/send/m.room.message/{txn_id}"

        msgtype = "m.image" if attachment.content_type.startswith("image/") else "m.file"
        if attachment.is_voice_note or attachment.content_type.startswith("audio/"):
            msgtype = "m.audio"

        payload = {
            "msgtype": msgtype,
            "body": attachment.name,
            "url": mxc_url,
            "info": {
                "size": attachment.size_bytes,
                "mimetype": attachment.content_type,
            },
        }

        self.http_request(
            url=url,
            method="PUT",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            timeout=self.timeout,
        )

    def join_room(self, room_id_or_alias: str) -> bool:
        """Join a Matrix room by ID or alias."""
        encoded = urllib.parse.quote(room_id_or_alias)
        url = f"{self.homeserver}/_matrix/client/v3/join/{encoded}"

        try:
            code, resp_bytes, _ = self.http_request(
                url=url, method="POST", data=b"{}", headers=self._headers(), timeout=self.timeout
            )
            success = 200 <= code < 300
            if success:
                self._joined_rooms.add(room_id_or_alias)
            return success
        except Exception as e:
            logger.error(f"Failed to join room '{room_id_or_alias}': {e}")
            return False

    def receive_events(self, timeout: float = 0.0) -> List[ChannelMessage]:
        """Poll /sync endpoint for new Matrix room timeline events."""
        if not self.access_token:
            return []

        params: dict[str, str] = {
            "timeout": str(int(timeout * 1000)),
            "filter": json.dumps({"room": {"timeline": {"limit": 20}}}),
        }
        if self._sync_token:
            params["since"] = self._sync_token

        url = f"{self.homeserver}/_matrix/client/v3/sync?{urllib.parse.urlencode(params)}"

        try:
            code, resp_bytes, _ = self.http_request(
                url=url,
                method="GET",
                headers=self._headers(),
                timeout=max(timeout + 5.0, 10.0),
            )

            if code != 200:
                return []

            sync_data = json.loads(resp_bytes.decode("utf-8"))
            self._sync_token = sync_data.get("next_batch")

            messages: List[ChannelMessage] = []
            rooms = sync_data.get("rooms", {}).get("join", {})

            for room_id, room_data in rooms.items():
                events = room_data.get("timeline", {}).get("events", [])
                for ev in events:
                    if ev.get("type") != "m.room.message":
                        continue

                    sender = ev.get("sender", "")
                    # Ignore events sent by ourselves
                    if sender == self.user_id:
                        continue

                    content = ev.get("content", {})
                    body = content.get("body", "")
                    event_id = ev.get("event_id", uuid.uuid4().hex)
                    origin_ts = float(ev.get("origin_server_ts", time.time() * 1000)) / 1000.0

                    msg = ChannelMessage(
                        message_id=event_id,
                        channel_name=self.name,
                        channel_type=self.channel_type.value,
                        sender_id=sender,
                        recipient_id=room_id,
                        content=body,
                        metadata={
                            "msgtype": content.get("msgtype"),
                            "formatted_body": content.get("formatted_body"),
                            "is_encrypted": ev.get("type") == "m.room.encrypted",
                        },
                        timestamp=origin_ts,
                        raw_payload=ev,
                        is_group=True,
                        group_id=room_id,
                    )
                    messages.append(msg)
                    self._dispatch_message(msg)

            return messages
        except Exception as e:
            logger.error(f"Matrix /sync error: {e}")
            return []

    def health_check(self) -> ChannelStatus:
        """Verify Matrix homeserver connectivity and account credentials."""
        start_time = time.monotonic()
        details: dict[str, Any] = {
            "homeserver": self.homeserver,
            "user_id": self.user_id,
        }

        if not self.access_token:
            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=False,
                latency_ms=0.0,
                details=details,
                error="Matrix access token not configured.",
            )

        try:
            url = f"{self.homeserver}/_matrix/client/v3/account/whoami"
            code, resp_bytes, _ = self.http_request(
                url=url, method="GET", headers=self._headers(), timeout=5.0
            )
            latency = (time.monotonic() - start_time) * 1000.0
            is_healthy = code == 200

            if is_healthy:
                try:
                    details["whoami"] = json.loads(resp_bytes.decode("utf-8"))
                except Exception:
                    pass

            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=is_healthy,
                latency_ms=latency,
                details=details,
                error=f"Matrix API error {code}" if not is_healthy else None,
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
