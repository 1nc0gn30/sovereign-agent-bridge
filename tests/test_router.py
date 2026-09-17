"""Tests for Unified Swarm Message Router in sovereign_agent_bridge.router."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import pytest

from sovereign_agent_bridge.channels.webhook_channel import WebhookChannelAdapter
from sovereign_agent_bridge.router import RingBuffer, SwarmMessage, SwarmRouter


def test_swarm_message_json_and_dict():
    """Test SwarmMessage serialization to dict and JSON."""
    msg = SwarmMessage(
        source_agent_id="agent-proponent",
        target_agent_id="agent-skeptic",
        topic="consensus.proposal",
        payload={"action": "deploy_patch", "version": "1.2.0"},
        priority=1,
        tags=["consensus", "audit"],
    )

    d = msg.to_dict()
    assert d["source_agent_id"] == "agent-proponent"
    assert d["priority"] == 1

    reconstructed = SwarmMessage.from_dict(d)
    assert reconstructed.topic == "consensus.proposal"
    assert reconstructed.payload["action"] == "deploy_patch"

    json_str = msg.to_json()
    from_json_msg = SwarmMessage.from_json(json_str)
    assert from_json_msg.id == msg.id


def test_ring_buffer_capacity_and_retrieval():
    """Test RingBuffer circular eviction and replay."""
    rb = RingBuffer(capacity=5)

    for i in range(10):
        msg = SwarmMessage(
            source_agent_id=f"agent-{i}",
            sequence_number=i,
            payload={"index": i},
        )
        rb.append(msg)

    # Buffer should retain only last 5 items (indices 5, 6, 7, 8, 9)
    recent = rb.get_recent(5)
    assert len(recent) == 5
    assert recent[0].sequence_number == 5
    assert recent[-1].sequence_number == 9

    # Filter since sequence number
    since = rb.get_since_sequence(sequence_number=7)
    assert len(since) == 2
    assert [m.sequence_number for m in since] == [8, 9]


def test_router_channel_registration():
    """Test adapter registration and lookup in SwarmRouter."""
    router = SwarmRouter()
    adapter = WebhookChannelAdapter("hook-alpha", config={"url": "http://127.0.0.1"})

    router.register_channel(adapter)
    assert router.get_channel("hook-alpha") is adapter

    router.unregister_channel("hook-alpha")
    assert router.get_channel("hook-alpha") is None


def test_router_topic_subscription_and_dispatch():
    """Test topic matching with wildcards and callback dispatch."""
    router = SwarmRouter()
    received: List[SwarmMessage] = []

    def on_event(msg: SwarmMessage):
        received.append(msg)

    sub_id = router.subscribe(agent_id_or_pattern="sub-1", topic_pattern="agent.mesh.*", callback=on_event)
    assert sub_id is not None

    # Matching topic broadcast
    msg1 = SwarmMessage(target_agent_id="broadcast", topic="agent.mesh.sync", payload={"state": "active"})
    router.dispatch(msg1)

    # Non-matching topic broadcast
    msg2 = SwarmMessage(target_agent_id="broadcast", topic="system.metrics", payload={"cpu": 12})
    router.dispatch(msg2)

    assert len(received) == 1
    assert received[0].topic == "agent.mesh.sync"

    # Unsubscribe test
    assert router.unsubscribe(sub_id, topic_pattern="agent.mesh.*") is True
    router.dispatch(SwarmMessage(target_agent_id="broadcast", topic="agent.mesh.update"))
    assert len(received) == 1  # No new messages received


def test_router_mailbox_queuing():
    """Test direct targeting and agent mailbox retrieval."""
    router = SwarmRouter()

    msg = SwarmMessage(
        source_agent_id="agent-alpha",
        target_agent_id="agent-beta",
        topic="private.task",
        payload={"task_id": "T-99"},
    )
    router.dispatch(msg)

    mailbox = router.fetch_mailbox(agent_id="agent-beta")
    assert len(mailbox) == 1
    assert mailbox[0].payload["task_id"] == "T-99"

    # Mailbox is drained on fetch
    empty_mb = router.fetch_mailbox(agent_id="agent-beta")
    assert len(empty_mb) == 0
