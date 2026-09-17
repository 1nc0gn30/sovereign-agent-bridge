"""Channel adapter subsystem for Sovereign Agent Bridge.

Exports all messaging adapters, data models, and factory utilities for Signal, SimpleX,
Telegram, Matrix, and Webhook channels.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from sovereign_agent_bridge.channels.base import (
    BaseChannelAdapter,
    ChannelAttachment,
    ChannelMessage,
    ChannelStatus,
    ChannelType,
    DeliveryReceipt,
)
from sovereign_agent_bridge.channels.matrix_channel import MatrixChannelAdapter
from sovereign_agent_bridge.channels.signal_channel import SignalChannelAdapter
from sovereign_agent_bridge.channels.simplex_channel import SimpleXChannelAdapter
from sovereign_agent_bridge.channels.telegram_channel import (
    TelegramChannelAdapter,
    escape_markdown_v2,
)
from sovereign_agent_bridge.channels.webhook_channel import (
    WebhookChannelAdapter,
    compute_hmac_sha256,
    verify_webhook_signature,
)

CHANNEL_ADAPTER_REGISTRY: Dict[str, Type[BaseChannelAdapter]] = {
    ChannelType.SIGNAL.value: SignalChannelAdapter,
    ChannelType.SIMPLEX.value: SimpleXChannelAdapter,
    ChannelType.TELEGRAM.value: TelegramChannelAdapter,
    ChannelType.MATRIX.value: MatrixChannelAdapter,
    ChannelType.WEBHOOK.value: WebhookChannelAdapter,
}


def get_channel_adapter_class(channel_type: str | ChannelType) -> Type[BaseChannelAdapter]:
    """Retrieve the adapter class for a given channel type name."""
    type_str = channel_type.value if isinstance(channel_type, ChannelType) else str(channel_type).lower()
    if type_str not in CHANNEL_ADAPTER_REGISTRY:
        raise ValueError(
            f"Unsupported channel type '{type_str}'. Available types: {list(CHANNEL_ADAPTER_REGISTRY.keys())}"
        )
    return CHANNEL_ADAPTER_REGISTRY[type_str]


def create_channel_adapter(
    name: str,
    channel_type: str | ChannelType,
    config: Optional[Dict[str, Any]] = None,
) -> BaseChannelAdapter:
    """Instantiate a channel adapter from type and configuration.

    Args:
        name: Unique name identifier for the channel instance.
        channel_type: Type identifier (e.g. 'signal', 'simplex', 'telegram', 'matrix', 'webhook').
        config: Configuration dictionary for the adapter.

    Returns:
        Configured BaseChannelAdapter instance.
    """
    adapter_cls = get_channel_adapter_class(channel_type)
    return adapter_cls(name=name, config=config)


__all__ = [
    "BaseChannelAdapter",
    "ChannelAttachment",
    "ChannelMessage",
    "ChannelStatus",
    "ChannelType",
    "DeliveryReceipt",
    "SignalChannelAdapter",
    "SimpleXChannelAdapter",
    "TelegramChannelAdapter",
    "escape_markdown_v2",
    "MatrixChannelAdapter",
    "WebhookChannelAdapter",
    "compute_hmac_sha256",
    "verify_webhook_signature",
    "CHANNEL_ADAPTER_REGISTRY",
    "get_channel_adapter_class",
    "create_channel_adapter",
]
