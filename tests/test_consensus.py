"""Tests for 3-Way Dialectic Consensus Arbitration in sovereign_agent_bridge.consensus."""

from __future__ import annotations

import pytest

from sovereign_agent_bridge.consensus import (
    ConsensusEngine,
    CritiquePoint,
    CritiqueSeverity,
    DialecticPhase,
    DialecticRole,
    DialecticSession,
    VerdictType,
)


def test_critique_point_serialization():
    """Test CritiquePoint creation and dict serialization."""
    point = CritiquePoint(
        severity=CritiqueSeverity.CRITICAL,
        category="security",
        description="Private key exposed in log output",
        suggested_mitigation="Sanitize credentials before logging",
    )

    d = point.to_dict()
    assert d["severity"] == "CRITICAL"
    assert d["category"] == "security"
    assert d["description"] == "Private key exposed in log output"

    restored = CritiquePoint.from_dict(d)
    assert restored.severity == CritiqueSeverity.CRITICAL
    assert restored.category == "security"


def test_full_dialectic_consensus_workflow_approval():
    """Test standard 3-Way Dialectic consensus reaching APPROVED verdict."""
    engine = ConsensusEngine()

    # 1. Initialize Session
    session = engine.create_session(
        topic="Deploy Post-Quantum Kyber-1024 Transport",
        proponent_id="agent-proponent",
        skeptic_id="agent-skeptic",
        arbitrator_id="agent-arbitrator",
    )
    assert session.phase == DialecticPhase.PROPOSAL

    # 2. Phase 1: Proponent Submits Thesis
    session = engine.submit_thesis(
        session_id=session.session_id,
        agent_id="agent-proponent",
        summary="Integrate Kyber-1024 to protect channel against quantum attacks.",
        proposal_details={"algorithm": "Kyber-1024", "target_channels": ["signal", "matrix"]},
        justification="Future-proofing agent communications.",
    )
    assert session.phase == DialecticPhase.CRITIQUE
    assert session.thesis is not None

    # 3. Phase 2: Skeptic Submits Antithesis Critiques
    critiques = [
        CritiquePoint(
            severity=CritiqueSeverity.LOW,
            category="performance",
            description="Adds 1.8KB to handshake packet.",
            suggested_mitigation="Enable header compression.",
        )
    ]
    session = engine.submit_antithesis(
        session_id=session.session_id,
        agent_id="agent-skeptic",
        summary="Payload size impact evaluated; manageable overhead.",
        critiques=critiques,
    )
    assert session.phase == DialecticPhase.REBUTTAL
    assert len(session.critiques) == 1

    # 4. Phase 3: Proponent Submits Rebuttal
    session = engine.submit_rebuttal(
        session_id=session.session_id,
        agent_id="agent-proponent",
        concessions=["Will enable zstandard compression for handshakes."],
        addressed_critiques=[session.critiques[0].id],
        rebuttals={"performance": "Compression reduces overhead to < 200 bytes."},
    )
    assert session.phase == DialecticPhase.SYNTHESIS

    # 5. Phase 4: Arbitrator Synthesizes Verdict
    report = engine.synthesize(
        session_id=session.session_id,
        agent_id="agent-arbitrator",
        rationale="Overhead mitigated by compression. Approved for production rollout.",
    )

    assert report.verdict in (VerdictType.APPROVED, VerdictType.AMENDED)
    assert report.confidence_score > 0.70
    assert "Dialectic Consensus Arbitration Report" in report.to_markdown()


def test_dialectic_consensus_permission_enforcement():
    """Verify unauthorized agents cannot submit phases out of turn."""
    engine = ConsensusEngine()

    session = engine.create_session(
        topic="Permission test",
        proponent_id="proponent-1",
        skeptic_id="skeptic-1",
        arbitrator_id="arbitrator-1",
    )

    # Impostor attempting to submit thesis should raise PermissionError
    with pytest.raises(PermissionError):
        engine.submit_thesis(
            session_id=session.session_id,
            agent_id="impostor-agent",
            summary="Unauthorized proposal",
            proposal_details={},
        )
