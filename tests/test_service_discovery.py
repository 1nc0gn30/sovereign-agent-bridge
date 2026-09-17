"""Tests for agent capability discovery and multi-sig consensus verification."""

from sovereign_agent_bridge.service_discovery import (
    AgentCapabilityProfile,
    MultiSigProposal,
    ServiceRegistry,
    compute_agent_signature,
    verify_multisig_consensus,
)


def test_service_registry_registration_and_lookup():
    registry = ServiceRegistry()
    profile1 = AgentCapabilityProfile(
        agent_id="agent-coder-1",
        display_name="Code Architect",
        capabilities=["code_generation", "python", "refactoring"],
        tools=["view_file", "replace_file_content"],
        max_concurrency=4,
        current_load=1,
    )
    profile2 = AgentCapabilityProfile(
        agent_id="agent-security-1",
        display_name="Security Auditor",
        capabilities=["security_audit", "vulnerability_scan"],
        tools=["run_command"],
        max_concurrency=2,
        current_load=0,
    )

    registry.register_agent(profile1)
    registry.register_agent(profile2)

    # Search by capability
    coders = registry.list_agents_by_capability("python")
    assert len(coders) == 1
    assert coders[0].agent_id == "agent-coder-1"

    # Route task
    best = registry.find_agent_for_task("code_generation", required_tools=["view_file"])
    assert best is not None
    assert best.agent_id == "agent-coder-1"

    # Non-existent tool
    none_agent = registry.find_agent_for_task("code_generation", required_tools=["deploy_docker"])
    assert none_agent is None

    # Summary
    summary = registry.get_registry_summary()
    assert summary["total_registered_agents"] == 2
    assert summary["unique_capabilities_count"] == 5


def test_service_registry_load_balancing():
    registry = ServiceRegistry()
    agent_busy = AgentCapabilityProfile(
        agent_id="busy-bot",
        display_name="Busy Bot",
        capabilities=["nlp"],
        max_concurrency=4,
        current_load=3,  # 75% load
    )
    agent_idle = AgentCapabilityProfile(
        agent_id="idle-bot",
        display_name="Idle Bot",
        capabilities=["nlp"],
        max_concurrency=4,
        current_load=1,  # 25% load
    )

    registry.register_agent(agent_busy)
    registry.register_agent(agent_idle)

    chosen = registry.find_agent_for_task("nlp")
    assert chosen is not None
    assert chosen.agent_id == "idle-bot"


def test_multisig_consensus_verification():
    keys = {
        "agent-1": "secret-key-alpha",
        "agent-2": "secret-key-beta",
        "agent-3": "secret-key-gamma",
    }
    payload_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    proposal = MultiSigProposal(
        proposal_id="prop-deploy-prod-001",
        action_type="deploy_production",
        payload_hash=payload_hash,
        threshold=2,  # 2-of-3 threshold
    )

    # Agent 1 signs
    proposal.signatures["agent-1"] = compute_agent_signature(keys["agent-1"], payload_hash)
    # Threshold not yet reached (1 < 2)
    assert not verify_multisig_consensus(proposal, keys)
    assert not proposal.is_approved

    # Agent 2 signs with invalid signature
    proposal.signatures["agent-2"] = "invalid_forged_signature_hex"
    assert not verify_multisig_consensus(proposal, keys)
    assert not proposal.is_approved

    # Agent 3 signs correctly
    proposal.signatures["agent-3"] = compute_agent_signature(keys["agent-3"], payload_hash)
    assert verify_multisig_consensus(proposal, keys)
    assert proposal.is_approved
