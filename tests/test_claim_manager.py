"""Tests for Distributed Claim Mutex Lock Manager in sovereign_agent_bridge."""

from __future__ import annotations

import time
import pytest

from sovereign_agent_bridge.mcp_server import FallbackClaimManager


def test_claim_acquisition_and_conflict():
    """Test acquiring a project claim lock and detecting conflicting claims."""
    cm = FallbackClaimManager()

    # Initial claim
    res = cm.claim(project_id="proj-alpha", agent_id="agent-01", ttl=60.0)
    assert res["success"] is True
    assert res["status"] == "ACQUIRED"
    assert res["project_id"] == "proj-alpha"

    # Conflicting claim by different agent
    conflict = cm.claim(project_id="proj-alpha", agent_id="agent-02", ttl=60.0)
    assert conflict["success"] is False
    assert conflict["status"] == "CONFLICT"
    assert conflict["held_by"] == "agent-01"

    # Same agent renewing/extending claim succeeds
    renewal = cm.claim(project_id="proj-alpha", agent_id="agent-01", ttl=120.0)
    assert renewal["success"] is True


def test_claim_release():
    """Test releasing a project lock."""
    cm = FallbackClaimManager()

    cm.claim(project_id="proj-beta", agent_id="agent-01", ttl=60.0)

    # Unauthorized agent attempt
    unauth = cm.release(project_id="proj-beta", agent_id="agent-impostor")
    assert unauth["success"] is False
    assert unauth["status"] == "FORBIDDEN"

    # Legitimate owner release
    released = cm.release(project_id="proj-beta", agent_id="agent-01")
    assert released["success"] is True
    assert released["status"] == "RELEASED"

    # Releasing non-existent project returns success
    empty_rel = cm.release(project_id="nonexistent-proj", agent_id="agent-01")
    assert empty_rel["success"] is True


def test_claim_handoff():
    """Test atomic handoff of a claim from one agent to another."""
    cm = FallbackClaimManager()

    cm.claim(project_id="proj-gamma", agent_id="agent-01", ttl=60.0)

    # Handoff to agent-02
    handoff_res = cm.handoff(
        project_id="proj-gamma",
        agent_id="agent-01",
        handoff_to="agent-02",
        ttl=90.0,
    )
    assert handoff_res["success"] is True
    assert handoff_res["status"] == "HANDED_OFF"
    assert handoff_res["new_owner"] == "agent-02"

    # Original owner can no longer release
    forbidden = cm.release(project_id="proj-gamma", agent_id="agent-01")
    assert forbidden["success"] is False


def test_list_claims_and_expiry():
    """Test listing active claims and TTL expiry filtering."""
    cm = FallbackClaimManager()

    # Short TTL claim
    cm.claim(project_id="proj-short", agent_id="agent-fast", ttl=0.1)
    cm.claim(project_id="proj-long", agent_id="agent-slow", ttl=300.0)

    time.sleep(0.15)  # Wait for short claim to expire

    active = cm.list_claims()
    assert len(active) == 1
    assert active[0]["project_id"] == "proj-long"
