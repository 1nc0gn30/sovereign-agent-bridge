"""SimpleX Chat Privacy Network Channel Adapter for Sovereign Agent Bridge.

Integrates with the SimpleX Chat privacy messaging protocol (SMP protocol) via
SimpleX Chat CLI (`simplex-chat`), JSON-RPC socket, or agent HTTP gateway. SimpleX
provides decentralized, metadata-free end-to-end communication without user identifiers.
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
from typing import Any, List, Optional, Sequence

from sovereign_agent_bridge.channels.base import (
    BaseChannelAdapter,
    ChannelAttachment,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
    DeliveryReceipt,
)

logger = logging.getLogger("sovereign_agent_bridge.channels.simplex")


class SimpleXChannelAdapter(BaseChannelAdapter):
    """Channel adapter for SimpleX Chat privacy network."""

    def __init__(self, name: str, config: Optional[dict[str, Any]] = None) -> None:
        """Initialize the SimpleX Chat adapter.

        Config keys:
            - mode: "cli" (default), "rest_gateway", or "socket"
            - cli_path: Path to simplex-chat executable (default: "simplex-chat")
            - profile: SimpleX profile name or directory (Optional)
            - gateway_url: Base URL for HTTP/REST gateway (e.g. "http://127.0.0.1:5225")
            - socket_host: Host for SimpleX JSON-RPC socket (e.g. "127.0.0.1")
            - socket_port: Port for SimpleX JSON-RPC socket (e.g. 5225)
            - auth_token: Optional bearer token for gateway authentication
            - timeout: Operation timeout in seconds (default: 15.0)
        """
        super().__init__(name, config)
        self.mode: str = str(self.config.get("mode", "cli")).lower()
        self.cli_path: str = str(self.config.get("cli_path", "simplex-chat"))
        self.profile: Optional[str] = self.config.get("profile")
        self.gateway_url: str = str(self.config.get("gateway_url", "http://127.0.0.1:5225")).rstrip("/")
        self.socket_host: str = str(self.config.get("socket_host", "127.0.0.1"))
        self.socket_port: int = int(self.config.get("socket_port", 5225))
        self.auth_token: Optional[str] = self.config.get("auth_token")
        self.timeout: float = float(self.config.get("timeout", 15.0))

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.SIMPLEX

    def format_payload(
        self, content: str, metadata: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Format a message for SimpleX Chat agent wire format."""
        meta = metadata or {}
        return {
            "type": "send_message",
            "body": content,
            "recipient": meta.get("recipient"),
            "group_id": meta.get("group_id"),
            "file_path": meta.get("file_path"),
            "quote_id": meta.get("reply_to_id"),
        }

    def send_message(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """Send a message to a SimpleX contact, connection address, or group."""
        msg_id = uuid.uuid4().hex
        meta = dict(metadata or {})

        try:
            if self.mode == "rest_gateway":
                return self._send_rest(recipient, content, attachments, meta, msg_id)
            elif self.mode == "socket":
                return self._send_socket(recipient, content, attachments, meta, msg_id)
            else:
                return self._send_cli(recipient, content, attachments, meta, msg_id)
        except Exception as e:
            logger.error(f"SimpleX send failed to '{recipient}': {e}", exc_info=True)
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
    ) -> DeliveryReceipt:
        """Send message via SimpleX REST/HTTP agent gateway."""
        url = f"{self.gateway_url}/v1/messages"
        payload: dict[str, Any] = {
            "recipient": recipient,
            "text": content,
        }

        if attachments:
            att_list = []
            for att in attachments:
                att_list.append(att.to_dict())
            payload["attachments"] = att_list

        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        data_bytes = json.dumps(payload).encode("utf-8")
        code, resp_bytes, _ = self.http_request(
            url=url, method="POST", data=data_bytes, headers=headers, timeout=self.timeout
        )

        try:
            resp_data = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
        except Exception:
            resp_data = {"raw": resp_bytes.decode("utf-8", errors="replace")}

        success = 200 <= code < 300
        return DeliveryReceipt(
            success=success,
            message_id=msg_id,
            recipient_id=recipient,
            channel_name=self.name,
            status="delivered" if success else "failed",
            error_message=None if success else f"Gateway status {code}: {resp_data}",
            raw_response=resp_data,
        )

    def _send_socket(
        self,
        recipient: str,
        content: str,
        attachments: Optional[Sequence[ChannelAttachment]],
        meta: dict[str, Any],
        msg_id: str,
    ) -> DeliveryReceipt:
        """Send message via SimpleX TCP JSON-RPC socket."""
        rpc_req = {
            "jsonrpc": "2.0",
            "method": "send_msg",
            "params": {
                "recipient": recipient,
                "text": content,
            },
            "id": msg_id,
        }

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect((self.socket_host, self.socket_port))
            sock.sendall((json.dumps(rpc_req) + "\n").encode("utf-8"))

            resp_buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp_buf += chunk
                if b"\n" in chunk:
                    break

        resp_json = json.loads(resp_buf.decode("utf-8"))
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
    ) -> DeliveryReceipt:
        """Send message via SimpleX Chat CLI command."""
        executable = shutil.which(self.cli_path) or self.cli_path

        # Simplex CLI command format: simplex-chat send @contact "message" or CLI batch
        cmd = [executable]
        if self.profile:
            cmd.extend(["-p", self.profile])

        # Formulate direct CLI command or pipe
        cmd.extend(["send", recipient, content])

        if attachments:
            for att in attachments:
                if att.path and os.path.exists(att.path):
                    cmd.extend(["--file", att.path])

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
        """Poll incoming events from SimpleX Chat."""
        if self.mode == "rest_gateway":
            return self._receive_rest(timeout)
        elif self.mode == "cli":
            return self._receive_cli(timeout)
        return []

    def _receive_rest(self, timeout: float) -> List[ChannelMessage]:
        """Poll messages from SimpleX REST gateway."""
        url = f"{self.gateway_url}/v1/messages"
        if timeout > 0:
            url += f"?timeout={int(timeout)}"

        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            code, resp_bytes, _ = self.http_request(
                url=url, method="GET", headers=headers, timeout=max(timeout + 5.0, 10.0)
            )
            if code != 200:
                return []

            data = json.loads(resp_bytes.decode("utf-8"))
            if not isinstance(data, list):
                return []

            messages: List[ChannelMessage] = []
            for item in data:
                sender = item.get("sender", "unknown")
                text = item.get("text", "")
                ts = float(item.get("timestamp", time.time()))

                msg = ChannelMessage(
                    message_id=item.get("id", uuid.uuid4().hex),
                    channel_name=self.name,
                    channel_type=self.channel_type.value,
                    sender_id=sender,
                    recipient_id="me",
                    content=text,
                    timestamp=ts,
                    raw_payload=item,
                    is_group=bool(item.get("group")),
                    group_id=item.get("group_id"),
                )
                messages.append(msg)
                self._dispatch_message(msg)

            return messages
        except Exception as e:
            logger.error(f"SimpleX REST receive error: {e}")
            return []

    def _receive_cli(self, timeout: float) -> List[ChannelMessage]:
        """Receive messages via SimpleX Chat CLI."""
        executable = shutil.which(self.cli_path) or self.cli_path
        cmd = [executable]
        if self.profile:
            cmd.extend(["-p", self.profile])
        cmd.extend(["receive", "--json"])

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
                    msg = ChannelMessage(
                        message_id=item.get("id", uuid.uuid4().hex),
                        channel_name=self.name,
                        channel_type=self.channel_type.value,
                        sender_id=item.get("sender", ""),
                        recipient_id="me",
                        content=item.get("text", item.get("content", "")),
                        timestamp=float(item.get("timestamp", time.time())),
                        raw_payload=item,
                    )
                    messages.append(msg)
                    self._dispatch_message(msg)
                except Exception:
                    continue

            return messages
        except Exception as e:
            logger.error(f"SimpleX CLI receive error: {e}")
            return []

    def health_check(self) -> ChannelStatus:
        """Verify SimpleX Chat connectivity."""
        start_time = time.monotonic()
        details: dict[str, Any] = {
            "mode": self.mode,
            "profile": self.profile,
        }

        try:
            if self.mode == "rest_gateway":
                url = f"{self.gateway_url}/v1/status"
                code, resp_bytes, _ = self.http_request(url=url, method="GET", timeout=5.0)
                latency = (time.monotonic() - start_time) * 1000.0
                is_healthy = code == 200
                if is_healthy:
                    try:
                        details["gateway_info"] = json.loads(resp_bytes.decode("utf-8"))
                    except Exception:
                        pass
                return ChannelStatus(
                    channel_name=self.name,
                    channel_type=self.channel_type.value,
                    is_healthy=is_healthy,
                    latency_ms=latency,
                    details=details,
                    error=f"Gateway status code {code}" if not is_healthy else None,
                )
            elif self.mode == "socket":
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(3.0)
                    sock.connect((self.socket_host, self.socket_port))
                latency = (time.monotonic() - start_time) * 1000.0
                return ChannelStatus(
                    channel_name=self.name,
                    channel_type=self.channel_type.value,
                    is_healthy=True,
                    latency_ms=latency,
                    details=details,
                )
            else:
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
