"""Telegram Bot API Channel Adapter for Sovereign Agent Bridge.

Provides Telegram Bot API integration using standard library HTTP requests,
supporting MarkdownV2 escaping, long-polling, webhook payload ingestion,
and multi-part attachment handling.
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import re
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

logger = logging.getLogger("sovereign_agent_bridge.channels.telegram")

# Characters requiring escape in Telegram MarkdownV2 outside of code blocks
MARKDOWN_V2_RESERVED = r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])"


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2 formatting.

    Escapes: _ * [ ] ( ) ~ ` > # + - = | { } . ! \\
    """
    return re.sub(MARKDOWN_V2_RESERVED, r"\\\1", text)


class TelegramChannelAdapter(BaseChannelAdapter):
    """Channel adapter for Telegram Bot API."""

    TELEGRAM_API_BASE = "https://api.telegram.org"
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the Telegram adapter.

        Config keys:
            - bot_token: Telegram Bot Token from @BotFather (Required)
            - api_base: Telegram Bot API base URL (default: "https://api.telegram.org")
            - default_chat_id: Fallback chat ID / group ID (Optional)
            - parse_mode: Default parse mode ("MarkdownV2", "HTML", or None) (default: "MarkdownV2")
            - timeout: Request timeout in seconds (default: 30.0)
        """
        super().__init__(name, config)
        self.bot_token: str = str(self.config.get("bot_token", "")).strip()
        self.api_base: str = str(self.config.get("api_base", self.TELEGRAM_API_BASE)).rstrip("/")
        self.default_chat_id: Optional[str] = self.config.get("default_chat_id")
        self.parse_mode: Optional[str] = self.config.get("parse_mode", "MarkdownV2")
        self.timeout: float = float(self.config.get("timeout", 30.0))
        self._last_update_id: int = 0

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.TELEGRAM

    def _get_url(self, method: str) -> str:
        """Construct Telegram Bot API endpoint URL."""
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    def format_payload(
        self, content: str, metadata: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Format a message payload for Telegram sendMessage API."""
        meta = metadata or {}
        chat_id = meta.get("chat_id") or meta.get("recipient") or self.default_chat_id
        parse_mode = meta.get("parse_mode", self.parse_mode)

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": content,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if meta.get("reply_to_message_id"):
            payload["reply_to_message_id"] = meta["reply_to_message_id"]
        if meta.get("disable_notification"):
            payload["disable_notification"] = True

        return payload

    def send_message(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """Send a message or media to a Telegram chat ID or username."""
        msg_id = uuid.uuid4().hex
        meta = dict(metadata or {})
        chat_id = recipient or meta.get("chat_id") or self.default_chat_id

        if not chat_id:
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id="",
                channel_name=self.name,
                status="failed",
                error_message="Recipient / chat_id not provided.",
                raw_response={},
            )

        if not self.bot_token:
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id=str(chat_id),
                channel_name=self.name,
                status="failed",
                error_message="Telegram bot_token is not configured.",
                raw_response={},
            )

        try:
            if attachments:
                return self._send_attachments(chat_id, content, attachments, meta, msg_id)
            else:
                return self._send_text_chunks(chat_id, content, meta, msg_id)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}", exc_info=True)
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id=str(chat_id),
                channel_name=self.name,
                status="failed",
                error_message=str(e),
                raw_response={"error": str(e)},
            )

    def _send_text_chunks(
        self, chat_id: str | int, content: str, meta: dict[str, Any], msg_id: str
    ) -> DeliveryReceipt:
        """Split text into 4096-char safe chunks and send sequentially."""
        parse_mode = meta.get("parse_mode", self.parse_mode)
        chunks = self._chunk_text(content, self.MAX_MESSAGE_LENGTH)
        last_response: dict[str, Any] = {}

        for idx, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if meta.get("reply_to_message_id") and idx == 0:
                payload["reply_to_message_id"] = meta["reply_to_message_id"]

            code, resp_data = self._api_call("sendMessage", payload)
            last_response = resp_data
            if code != 200 or not resp_data.get("ok"):
                # If MarkdownV2 parsing fails, retry without parse_mode as plain text
                if parse_mode:
                    payload.pop("parse_mode", None)
                    code, resp_data = self._api_call("sendMessage", payload)
                    last_response = resp_data
                    if code == 200 and resp_data.get("ok"):
                        continue

                return DeliveryReceipt(
                    success=False,
                    message_id=msg_id,
                    recipient_id=str(chat_id),
                    channel_name=self.name,
                    status="failed",
                    error_message=f"Telegram API error {code}: {resp_data.get('description')}",
                    raw_response=last_response,
                )

        return DeliveryReceipt(
            success=True,
            message_id=msg_id,
            recipient_id=str(chat_id),
            channel_name=self.name,
            status="delivered",
            raw_response=last_response,
        )

    def _send_attachments(
        self,
        chat_id: str | int,
        caption: str,
        attachments: Sequence[ChannelAttachment],
        meta: dict[str, Any],
        msg_id: str,
    ) -> DeliveryReceipt:
        """Upload and send media attachments via multipart/form-data."""
        last_resp: dict[str, Any] = {}
        for att in attachments:
            content_type = att.content_type.lower()
            file_bytes = att.get_bytes()
            filename = att.name or "file.dat"

            if att.is_voice_note or content_type.startswith("audio/"):
                method = "sendVoice" if att.is_voice_note else "sendAudio"
                file_field = "voice" if att.is_voice_note else "audio"
            elif content_type.startswith("image/"):
                method = "sendPhoto"
                file_field = "photo"
            else:
                method = "sendDocument"
                file_field = "document"

            fields = {
                "chat_id": str(chat_id),
                "caption": caption[:1024] if caption else "",
            }
            if meta.get("parse_mode", self.parse_mode):
                fields["parse_mode"] = meta.get("parse_mode", self.parse_mode)

            code, resp_data = self._multipart_post(
                method=method,
                fields=fields,
                file_field=file_field,
                filename=filename,
                file_bytes=file_bytes,
                content_type=content_type,
            )
            last_resp = resp_data
            if code != 200 or not resp_data.get("ok"):
                return DeliveryReceipt(
                    success=False,
                    message_id=msg_id,
                    recipient_id=str(chat_id),
                    channel_name=self.name,
                    status="failed",
                    error_message=f"Attachment send failed: {resp_data.get('description')}",
                    raw_response=last_resp,
                )

        return DeliveryReceipt(
            success=True,
            message_id=msg_id,
            recipient_id=str(chat_id),
            channel_name=self.name,
            status="delivered",
            raw_response=last_resp,
        )

    def _api_call(self, method: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Perform JSON POST request to Telegram API."""
        url = self._get_url(method)
        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        code, resp_bytes, _ = self.http_request(
            url=url, method="POST", data=data_bytes, headers=headers, timeout=self.timeout
        )

        try:
            resp_data = json.loads(resp_bytes.decode("utf-8"))
        except Exception:
            resp_data = {"raw": resp_bytes.decode("utf-8", errors="replace")}

        return code, resp_data

    def _multipart_post(
        self,
        method: str,
        fields: dict[str, str],
        file_field: str,
        filename: str,
        file_bytes: bytes,
        content_type: str,
    ) -> tuple[int, dict[str, Any]]:
        """Send multipart/form-data upload to Telegram API using standard library."""
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        body = io.BytesIO()

        # Write text fields
        for key, val in fields.items():
            if val is not None:
                body.write(f"--{boundary}\r\n".encode("utf-8"))
                body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
                body.write(f"{val}\r\n".encode("utf-8"))

        # Write file field
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.write(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.write(file_bytes)
        body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode("utf-8"))

        req_bytes = body.getvalue()
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(req_bytes)),
        }

        url = self._get_url(method)
        code, resp_bytes, _ = self.http_request(
            url=url, method="POST", data=req_bytes, headers=headers, timeout=self.timeout
        )

        try:
            resp_data = json.loads(resp_bytes.decode("utf-8"))
        except Exception:
            resp_data = {"raw": resp_bytes.decode("utf-8", errors="replace")}

        return code, resp_data

    def receive_events(self, timeout: float = 0.0) -> List[ChannelMessage]:
        """Poll Telegram getUpdates API for new incoming updates."""
        if not self.bot_token:
            return []

        payload: dict[str, Any] = {
            "offset": self._last_update_id + 1,
            "timeout": int(min(timeout, 30.0)),
            "allowed_updates": ["message", "edited_message", "channel_post", "callback_query"],
        }

        code, resp_data = self._api_call("getUpdates", payload)
        if code != 200 or not resp_data.get("ok"):
            return []

        updates = resp_data.get("result", [])
        messages: List[ChannelMessage] = []

        for upd in updates:
            upd_id = upd.get("update_id", 0)
            if upd_id > self._last_update_id:
                self._last_update_id = upd_id

            msg_obj = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
            if not msg_obj:
                continue

            parsed_msg = self._parse_telegram_message(msg_obj, upd)
            if parsed_msg:
                messages.append(parsed_msg)
                self._dispatch_message(parsed_msg)

        return messages

    def process_webhook_update(self, payload: dict[str, Any] | str) -> List[ChannelMessage]:
        """Process incoming webhook JSON payload from Telegram."""
        if isinstance(payload, str):
            payload = json.loads(payload)

        msg_obj = payload.get("message") or payload.get("edited_message") or payload.get("channel_post")
        if not msg_obj:
            return []

        msg = self._parse_telegram_message(msg_obj, payload)
        if msg:
            self._dispatch_message(msg)
            return [msg]
        return []

    def _parse_telegram_message(
        self, msg_obj: dict[str, Any], raw_payload: dict[str, Any]
    ) -> Optional[ChannelMessage]:
        """Parse raw Telegram message JSON into ChannelMessage dataclass."""
        chat = msg_obj.get("chat", {})
        sender = msg_obj.get("from", {})
        chat_id = str(chat.get("id", ""))
        sender_id = str(sender.get("id", chat_id))
        text = msg_obj.get("text") or msg_obj.get("caption") or ""
        is_group = chat.get("type") in ("group", "supergroup", "channel")
        timestamp = float(msg_obj.get("date", time.time()))

        attachments: List[ChannelAttachment] = []

        # Handle photos (select largest resolution)
        if "photo" in msg_obj and isinstance(msg_obj["photo"], list) and msg_obj["photo"]:
            best_photo = msg_obj["photo"][-1]
            attachments.append(
                ChannelAttachment(
                    name=f"photo_{best_photo.get('file_id')}.jpg",
                    content_type="image/jpeg",
                    path=best_photo.get("file_id"),
                    size_bytes=int(best_photo.get("file_size", 0)),
                )
            )

        # Handle documents
        if "document" in msg_obj:
            doc = msg_obj["document"]
            attachments.append(
                ChannelAttachment(
                    name=doc.get("file_name", "document"),
                    content_type=doc.get("mime_type", "application/octet-stream"),
                    path=doc.get("file_id"),
                    size_bytes=int(doc.get("file_size", 0)),
                )
            )

        # Handle voice notes
        if "voice" in msg_obj:
            voice = msg_obj["voice"]
            attachments.append(
                ChannelAttachment(
                    name="voice_note.ogg",
                    content_type=voice.get("mime_type", "audio/ogg"),
                    path=voice.get("file_id"),
                    is_voice_note=True,
                    size_bytes=int(voice.get("file_size", 0)),
                )
            )

        reply_to_id = None
        if "reply_to_message" in msg_obj:
            reply_to_id = str(msg_obj["reply_to_message"].get("message_id"))

        return ChannelMessage(
            message_id=str(msg_obj.get("message_id", uuid.uuid4().hex)),
            channel_name=self.name,
            channel_type=self.channel_type.value,
            sender_id=sender_id,
            recipient_id=chat_id,
            content=text,
            attachments=attachments,
            metadata={
                "username": sender.get("username"),
                "first_name": sender.get("first_name"),
                "chat_type": chat.get("type"),
                "chat_title": chat.get("title"),
            },
            timestamp=timestamp,
            raw_payload=raw_payload,
            is_group=is_group,
            group_id=chat_id if is_group else None,
            reply_to_id=reply_to_id,
        )

    def health_check(self) -> ChannelStatus:
        """Inspect bot connectivity and info via getMe endpoint."""
        start_time = time.monotonic()
        details: dict[str, Any] = {"token_configured": bool(self.bot_token)}

        if not self.bot_token:
            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=False,
                latency_ms=0.0,
                details=details,
                error="Bot token not configured.",
            )

        try:
            code, resp_data = self._api_call("getMe", {})
            latency = (time.monotonic() - start_time) * 1000.0
            is_healthy = code == 200 and resp_data.get("ok", False)
            if is_healthy:
                details["bot_info"] = resp_data.get("result", {})

            return ChannelStatus(
                channel_name=self.name,
                channel_type=self.channel_type.value,
                is_healthy=is_healthy,
                latency_ms=latency,
                details=details,
                error=resp_data.get("description") if not is_healthy else None,
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

    @staticmethod
    def _chunk_text(text: str, max_size: int) -> List[str]:
        """Split text into chunks without breaking lines unnecessarily."""
        if len(text) <= max_size:
            return [text]

        chunks = []
        lines = text.split("\n")
        current_chunk = ""

        for line in lines:
            if len(current_chunk) + len(line) + 1 > max_size:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                # If a single line is greater than max_size, break it
                while len(line) > max_size:
                    chunks.append(line[:max_size])
                    line = line[max_size:]
                current_chunk = line
            else:
                if current_chunk:
                    current_chunk += "\n" + line
                else:
                    current_chunk = line

        if current_chunk:
            chunks.append(current_chunk)

        return chunks or [text]
