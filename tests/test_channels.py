"""Unit tests for Channel Adapters in sovereign_agent_bridge.channels."""

from __future__ import annotations

import base64
import json
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict

import pytest

from sovereign_agent_bridge.channels import (
    CHANNEL_ADAPTER_REGISTRY,
    BaseChannelAdapter,
    ChannelAttachment,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
    DeliveryReceipt,
    MatrixChannelAdapter,
    SignalChannelAdapter,
    SimpleXChannelAdapter,
    TelegramChannelAdapter,
    WebhookChannelAdapter,
    compute_hmac_sha256,
    create_channel_adapter,
    escape_markdown_v2,
    get_channel_adapter_class,
    verify_webhook_signature,
)


def test_channel_attachment_serialization(tmp_path: Path):
    """Test ChannelAttachment creation, base64 conversion, and dict export."""
    raw_data = b"Agent instruction manifest: SEC-01"
    att = ChannelAttachment(name="manifest.txt", content_type="text/plain", data=raw_data)

    assert att.name == "manifest.txt"
    assert att.size_bytes == len(raw_data)
    assert att.get_bytes() == raw_data
    assert att.get_base64() == base64.b64encode(raw_data).decode("ascii")

    d = att.to_dict()
    assert d["name"] == "manifest.txt"
    assert d["size_bytes"] == len(raw_data)

    # Reconstruct from dict
    restored = ChannelAttachment.from_dict(d)
    assert restored.name == att.name
    assert restored.get_bytes() == raw_data

    # Test file path initialization
    test_file = tmp_path / "attached.bin"
    test_file.write_bytes(raw_data)
    file_att = ChannelAttachment(name="attached.bin", path=str(test_file))
    assert file_att.size_bytes == len(raw_data)
    assert file_att.get_bytes() == raw_data


def test_channel_message_serialization():
    """Test ChannelMessage serialization and deserialization."""
    msg = ChannelMessage(
        message_id="msg-101",
        channel_name="signal-alpha",
        channel_type=ChannelType.SIGNAL.value,
        sender_id="+15551112222",
        recipient_id="+15553334444",
        content="Coordinates updated.",
        metadata={"priority": "CRITICAL"},
        is_group=True,
        group_id="group.test-mesh",
    )

    d = msg.to_dict()
    assert d["message_id"] == "msg-101"
    assert d["is_group"] is True

    reconstructed = ChannelMessage.from_dict(d)
    assert reconstructed.message_id == msg.message_id
    assert reconstructed.recipient_id == msg.recipient_id
    assert reconstructed.group_id == msg.group_id
    assert reconstructed.content == msg.content


def test_channel_factory_and_registry():
    """Test channel adapter class resolution and factory creation."""
    assert len(CHANNEL_ADAPTER_REGISTRY) >= 5

    cls = get_channel_adapter_class("signal")
    assert cls is SignalChannelAdapter

    adapter = create_channel_adapter(
        name="sig-test",
        channel_type="signal",
        config={"account": "+15550000000", "endpoint": "http://127.0.0.1:8080"},
    )
    assert isinstance(adapter, SignalChannelAdapter)
    assert adapter.name == "sig-test"
    assert adapter.channel_type == ChannelType.SIGNAL

    with pytest.raises(ValueError, match="Unsupported channel type"):
        get_channel_adapter_class("unsupported_protocol_xyz")


def test_telegram_escape_markdown_v2():
    """Test Telegram MarkdownV2 character escaping utility."""
    raw = "Alert: Agent [Alpha] failed *dead-man* switch! Cost = $10 (100% loss)."
    escaped = escape_markdown_v2(raw)
    assert r"\[" in escaped
    assert r"\]" in escaped
    assert r"\*" in escaped
    assert r"\(" in escaped
    assert r"\)" in escaped
    assert r"\!" in escaped


def test_webhook_hmac_computation_and_verification():
    """Test HMAC-SHA256 signature computation and constant-time verification."""
    payload = b'{"event": "agent_pulse", "agent_id": "agent-007"}'
    secret = "top-secret-sovereign-key-99"

    sig = compute_hmac_sha256(payload, secret)
    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA-256 hex digest length

    # Verify signature
    assert verify_webhook_signature(payload, sig, secret) is True
    assert verify_webhook_signature(payload, "invalid_sig" + ("0" * 53), secret) is False

    # Verify with sha256= prefix
    assert verify_webhook_signature(payload, f"sha256={sig}", secret) is True


def test_signal_adapter_format_payload_and_lifecycle():
    """Test SignalChannelAdapter payload formatting and lifecycle."""
    adapter = SignalChannelAdapter(
        name="signal-main",
        config={"account": "+15551234567", "endpoint": "http://127.0.0.1:8080"},
    )
    payload = adapter.format_payload("Test message", metadata={"recipient": "+15559876543"})
    assert payload["number"] == "+15551234567"
    assert payload["message"] == "Test message"
    assert payload["recipients"] == ["+15559876543"]

    # Lifecycle start/stop
    adapter.start()
    assert adapter.is_running is True
    adapter.stop()
    assert adapter.is_running is False


def test_signal_adapter_mock_send():
    """Test SignalChannelAdapter send_message with mocked HTTP transport."""
    adapter = SignalChannelAdapter(
        name="signal-main",
        config={"account": "+15551234567", "endpoint": "http://127.0.0.1:8080", "mode": "rest"},
    )

    with mock.patch.object(adapter, "http_request", return_value=(200, b'{"timestamp": 123456}', {})):
        receipt = adapter.send_message(recipient="+15559998888", content="Hello via Signal")
        assert receipt.success is True
        assert receipt.status == "delivered"
        assert receipt.recipient_id == "+15559998888"


def test_webhook_adapter_mock_send():
    """Test WebhookChannelAdapter send_message with signed payload."""
    adapter = WebhookChannelAdapter(
        name="webhook-relay",
        config={"url": "http://127.0.0.1:9090/webhook", "secret": "sec-123"},
    )

    with mock.patch.object(adapter, "http_request", return_value=(200, b'{"status": "received"}', {})):
        receipt = adapter.send_message(recipient="endpoint", content="Webhook alert payload")
        assert receipt.success is True
        assert receipt.status == "delivered"


def test_message_callback_dispatch():
    """Test adapter on_message callback dispatching."""
    adapter = WebhookChannelAdapter(name="hook-cb", config={"url": "http://localhost"})
    received_messages = []

    def handle_msg(msg: ChannelMessage):
        received_messages.append(msg)

    adapter.on_message(handle_msg)

    test_msg = ChannelMessage(message_id="cb-01", content="Callback test")
    adapter._dispatch_message(test_msg)

    assert len(received_messages) == 1
    assert received_messages[0].message_id == "cb-01"
