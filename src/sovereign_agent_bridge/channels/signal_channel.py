"""Signal Messenger Channel Adapter for Sovereign Agent Bridge.

Supports signal-cli daemon (REST API mode / JSON-RPC) and direct signal-cli execution.
Handles direct messages, group messages, file/media attachments, and voice notes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, List, Optional, Sequence

from sovereign_agent_bridge.channels.base import (
    BaseChannelAdapter,
    ChannelAttachment,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
    DeliveryReceipt,
)

logger = logging.getLogger("sovereign_agent_bridge.channels.signal")


class SignalChannelAdapter(BaseChannelAdapter):
    """Channel adapter for Signal Messenger via signal-cli REST API or CLI/socket."""

    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the Signal adapter.

        Config keys:
            - account: Registered phone number on Signal (e.g. "+15551234567") (Required)
            - endpoint: REST API base URL (e.g. "http://127.0.0.1:8080") (Optional)
            - socket_path: UNIX domain socket path for signal-cli (Optional)
            - cli_path: Path to signal-cli executable (Optional, defaults to "signal-cli")
            - mode: "rest" (default), "jsonrpc_socket", or "cli"
            - timeout: HTTP / command timeout in seconds (default: 15.0)
        """
        super().__init__(name, config)
        self.account: str = str(self.config.get("account", ""))
        self.endpoint: str = str(self.config.get("endpoint", "http://127.0.0.1:8080")).rstrip("/")
        self.socket_path: Optional[str] = self.config.get("socket_path")
        self.cli_path: str = str(self.config.get("cli_path", "signal-cli"))
        self.mode: str = str(self.config.get("mode", "rest")).lower()
        self.timeout: float = float(self.config.get("timeout", 15.0))

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.SIGNAL

    def format_payload(
        self, content: str, metadata: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Format message into signal-cli REST payload structure."""
        meta = metadata or {}
        payload: dict[str, Any] = {
            "message": content,
            "number": self.account,
        }
        if meta.get("recipient"):
            payload["recipients"] = [meta["recipient"]]
        elif meta.get("group_id"):
            payload["recipients"] = [meta["group_id"]]

        if meta.get("attachments"):
            payload["base64_attachments"] = meta["attachments"]

        return payload

    def send_message(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """Send a message to a Signal contact or group."""
        msg_id = uuid.uuid4().hex
        meta = dict(metadata or {})
        is_group = recipient.startswith("group.") or meta.get("is_group", False)

        try:
            if self.mode == "rest":
                return self._send_rest(recipient, content, attachments, meta, msg_id, is_group)
            elif self.mode == "jsonrpc_socket" and self.socket_path:
                return self._send_socket(recipient, content, attachments, meta, msg_id, is_group)
            else:
                return self._send_cli(recipient, content, attachments, meta, msg_id, is_group)
        except Exception as e:
            logger.error(f"Signal send failed to '{recipient}': {e}", exc_info=True)
            return DeliveryReceipt(
                success=False,
                message_id=msg_id,
                recipient_id=recipient,
                channel_name=self.name,
                status="failed",
                error_message=str(e),
                raw_response={"error": str(e)},
            )

    def _send_rest(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]],
        meta: dict[str, Any],
        msg_id: str,
        is_group: bool,
    ) -> DeliveryReceipt:
        """Send message via signal-cli-rest-api daemon."""
        url = f"{self.endpoint}/v2/send"
        base64_atts: List[str] = []

        if attachments:
            for att in attachments:
                base64_atts.append(att.get_base64())

        payload: dict[str, Any] = {
            "message": content,
            "number": self.account,
            "recipients": [recipient],
        }

        if base64_atts:
            payload["base64_attachments"] = base64_atts

        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        code, resp_bytes, _ = self.http_request(
            url=url, method="POST", data=data_bytes, headers=headers, timeout=self.timeout
        )

        try:
            resp_data = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
        except Exception:
            resp_data = {"raw": resp_bytes.decode("utf-8", errors="replace")}

        success = 200 <= code < 300
        status = "delivered" if success else "failed"
        err_msg = None if success else f"HTTP status {code}: {resp_data}"

        return DeliveryReceipt(
            success=success,
            message_id=msg_id,
            recipient_id=recipient,
            channel_name=self.name,
            status=status,
            error_message=err_msg,
            raw_response=resp_data,
        )

    def _send_socket(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]],
        meta: dict[str, Any],
        msg_id: str,
        is_group: bool,
    ) -> DeliveryReceipt:
        """Send message via signal-cli JSON-RPC Unix domain socket."""
        if not self.socket_path or not os.path.exists(self.socket_path):
            raise FileNotFoundError(f"Signal socket not found at: {self.socket_path}")

        params: dict[str, Any] = {
            "account": self.account,
            "message": content,
        }
        if is_group:
            params["groupId"] = recipient.removeprefix("group.")
        else:
            params["recipient"] = [recipient]

        if attachments:
            paths: List[str] = []
            for att in attachments:
                if att.path:
                    paths.append(att.path)
            if paths:
                params["attachments"] = paths

        rpc_request = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": params,
            "id": msg_id,
        }

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            sock.sendall((json.dumps(rpc_request) + "\n").encode("utf-8"))

            response_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b"\n" in chunk:
                    break

        resp_json = json.loads(response_data.decode("utf-8"))
        has_error = "error" in resp_json
        return DeliveryReceipt(
            success=not has_error,
            message_id=msg_id,
            recipient_id=recipient,
            channel_name=self.name,
            status="failed" if has_error else "delivered",
            error_message=str(resp_json.get("error")) if has_error else None,
            raw_response=resp_json,
        )

    def _send_cli(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]],
        meta: dict[str, Any],
        msg_id: str,
        is_group: bool,
    ) -> DeliveryReceipt:
        """Send message directly via signal-cli command line invocation."""
        executable = shutil.which(self.cli_path) or self.cli_path
        cmd = [executable, "-u", self.account, "send", "-m", content]

        if attachments:
            for att in attachments:
                if att.path and os.path.exists(att.path):
                    cmd.extend(["-a", att.path])

        if is_group:
            cmd.extend(["-g", recipient.removeprefix("group.")])
        else:
            cmd.append(recipient)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

        success = proc.returncode == 0
        return DeliveryReceipt(
            success=success,
            message_id=msg_id,
            recipient_id=recipient,
            channel_name=self.name,
            status="delivered" if success else "failed",
            error_message=proc.stderr.strip() if not success else None,
            raw_response={"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode},
        )

    def receive_events(self, timeout: float = 0.0) -> List[ChannelMessage]:
        """Poll and parse received messages from signal-cli."""
        if self.mode == "rest":
            return self._receive_rest(timeout)
        elif self.mode == "cli":
            return self._receive_cli(timeout)
        return []

    def _receive_rest(self, timeout: float) -> List[ChannelMessage]:
        """Poll incoming messages from signal-cli-rest-api."""
        if not self.account:
            return []

        url = f"{self.endpoint}/v1/receive/{urllib.parse.quote(self.account)}"
        query = {"timeout": str(int(timeout))} if timeout > 0 else {}
        if query:
            url += f"?{urllib.parse.urlencode(query)}"

        try:
            code, resp_bytes, _ = self.http_request(url=url, method="GET", timeout=max(timeout + 5.0, 10.0))
            if code != 200:
                return []

            data = json.loads(resp_bytes.decode("utf-8"))
            if not isinstance(data, list):
                return []

            messages: List[ChannelMessage] = []
            for item in data:
                envelope = item.get("envelope", {})
                data_msg = envelope.get("dataMessage", {})
                if not data_msg and not envelope.get("syncMessage"):
                    continue

                source = envelope.get("source", "")
                text = data_msg.get("message", "")
                ts = float(envelope.get("timestamp", time.time() * 1000)) / 1000.0

                group_info = data_msg.get("groupInfo", {})
                is_group = bool(group_info)
                group_id = group_info.get("groupId")

                # Parse attachments
                attachments: List[ChannelAttachment] = []
                for att in data_msg.get("attachments", []):
                    attachments.append(
                        ChannelAttachment(
                            name=att.get("filename", "attachment"),
                            content_type=att.get("contentType", "application/octet-stream"),
                            size_bytes=int(att.get("size", 0)),
                            is_voice_note=bool(att.get("voiceNote", False)),
                        )
                    )

                msg = ChannelMessage(
                    message_id=uuid.uuid4().hex,
                    channel_name=self.name,
                    channel_type=self.channel_type.value,
                    sender_id=source,
                    recipient_id=self.account,
                    content=text,
                    attachments=attachments,
                    metadata={"timestamp_ms": envelope.get("timestamp")},
                    timestamp=ts,
                    raw_payload=item,
                    is_group=is_group,
                    group_id=f"group.{group_id}" if group_id else None,
                )
                messages.append(msg)
                self._dispatch_message(msg)

            return messages
        except Exception as e:
            logger.error(f"Error receiving Signal events: {e}")
            return []

    def _receive_cli(self, timeout: float) -> List[ChannelMessage]:
        """Poll incoming messages from signal-cli subprocess."""
        if not self.account:
            return []

        executable = shutil.which(self.cli_path) or self.cli_path
        cmd = [executable, "-u", self.account, "--output=json", "receive"]
        if timeout > 0:
            cmd.extend(["-t", str(timeout)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(timeout + 5.0, 15.0),
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return []

            messages: List[ChannelMessage] = []
            for line in proc.stdout.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    envelope = item.get("envelope", {})
                    data_msg = envelope.get("dataMessage", {})
                    if not data_msg:
                        continue

                    source = envelope.get("source", "")
                    text = data_msg.get("message", "")
                    ts = float(envelope.get("timestamp", time.time() * 1000)) / 1000.0

                    group_info = data_msg.get("groupInfo", {})
                    is_group = bool(group_info)
                    group_id = group_info.get("groupId")

                    msg = ChannelMessage(
                        message_id=uuid.uuid4().hex,
                        channel_name=self.name,
                        channel_type=self.channel_type.value,
                        sender_id=source,
                        recipient_id=self.account,
                        content=text,
                        timestamp=ts,
                        raw_payload=item,
                        is_group=is_group,
                        group_id=f"group.{group_id}" if group_id else None,
                    )
                    messages.append(msg)
                    self._dispatch_message(msg)
                except Exception:
                    continue

            return messages
        except Exception as e:
            logger.error(f"CLI receive failed: {e}")
            return []

    def health_check(self) -> ChannelStatus:
        """Inspect health of the Signal channel adapter."""
        start_time = time.monotonic()
        details: dict[str, Any] = {
            "mode": self.mode,
            "account": self.account,
            "endpoint": self.endpoint if self.mode == "rest" else None,
        }

        try:
            if self.mode == "rest":
                url = f"{self.endpoint}/v1/about"
                code, resp_bytes, _ = self.http_request(url=url, method="GET", timeout=5.0)
                latency = (time.monotonic() - start_time) * 1000.0
                if code == 200:
                    try:
                        details["about"] = json.loads(resp_bytes.decode("utf-8"))
                    except Exception:
                        pass
                    return ChannelStatus(
                        channel_name=self.name,
                        channel_type=self.channel_type.value,
                        is_healthy=True,
                        latency_ms=latency,
                        details=details,
                    )
                else:
                    return ChannelStatus(
                        channel_name=self.name,
                        channel_type=self.channel_type.value,
                        is_healthy=False,
                        latency_ms=latency,
                        details=details,
                        error=f"REST endpoint returned status {code}",
                    )
            elif self.mode == "cli":
                executable = shutil.which(self.cli_path) or self.cli_path
                proc = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5.0)
                latency = (time.monotonic() - start_time) * 1000.0
                healthy = proc.returncode == 0
                if healthy:
                    details["cli_version"] = proc.stdout.strip()
                return ChannelStatus(
                    channel_name=self.name,
                    channel_type=self.channel_type.value,
                    is_healthy=healthy,
                    latency_ms=latency,
                    details=details,
                    error=proc.stderr.strip() if not healthy else None,
                )
            else:
                latency = (time.monotonic() - start_time) * 1000.0
                return ChannelStatus(
                    channel_name=self.name,
                    channel_type=self.channel_type.value,
                    is_healthy=True,
                    latency_ms=latency,
                    details=details,
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
