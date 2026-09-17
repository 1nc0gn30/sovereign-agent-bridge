"""3-Way Dialectic Consensus Arbitration Engine for Sovereign Agent Bridge.

Implements a structured tripartite multi-agent dialectic consensus workflow:
  1. Proponent (Thesis) - Submits proposal, rationale, and intended modifications.
  2. Skeptic (Antithesis) - Audits proposal, surfaces failure modes, risks, and counter-arguments.
  3. Rebuttal (Refinement) - Proponent responds with concrete mitigations and concessions.
  4. Arbitrator (Synthesis) - Evaluates debate, calculates weighted confidence score, and renders verdict.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sovereign_agent_bridge.consensus")


class DialecticRole(str, Enum):
    """Roles in the 3-Way Dialectic Consensus Engine."""

    PROPONENT = "proponent"
    SKEPTIC = "skeptic"
    ARBITRATOR = "arbitrator"


class DialecticPhase(str, Enum):
    """Phases of the dialectic arbitration lifecycle."""

    PROPOSAL = "proposal"
    CRITIQUE = "critique"
    REBUTTAL = "rebuttal"
    SYNTHESIS = "synthesis"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class VerdictType(str, Enum):
    """Consensus verdict outcomes."""

    APPROVED = "APPROVED"
    AMENDED = "AMENDED"
    REJECTED = "REJECTED"
    ESCALATE = "ESCALATE"


class CritiqueSeverity(str, Enum):
    """Severity classification for skeptic critiques."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


SEVERITY_WEIGHTS: Dict[CritiqueSeverity, float] = {
    CritiqueSeverity.CRITICAL: 0.35,
    CritiqueSeverity.HIGH: 0.20,
    CritiqueSeverity.MEDIUM: 0.10,
    CritiqueSeverity.LOW: 0.05,
    CritiqueSeverity.INFO: 0.0,
}


@dataclass
class ProposalDraft:
    """Draft proposal container for consensus review."""

    title: str = ""
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    justification: str = ""
    proponent_id: str = ""
    target_channels: List[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CritiquePoint:
    """Individual critique or risk identified by the Skeptic agent."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    severity: CritiqueSeverity = CritiqueSeverity.MEDIUM
    category: str = "security"  # security, stability, architecture, compliance, logic
    description: str = ""
    suggested_mitigation: Optional[str] = None
    is_addressed: bool = False
    mitigation_notes: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "category": self.category,
            "description": self.description,
            "suggested_mitigation": self.suggested_mitigation,
            "is_addressed": self.is_addressed,
            "mitigation_notes": self.mitigation_notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CritiquePoint:
        sev = data.get("severity", "MEDIUM")
        return cls(
            id=data.get("id", uuid.uuid4().hex[:8]),
            severity=CritiqueSeverity(sev.upper()) if isinstance(sev, str) else CritiqueSeverity.MEDIUM,
            category=data.get("category", "security"),
            description=data.get("description", ""),
            suggested_mitigation=data.get("suggested_mitigation"),
            is_addressed=bool(data.get("is_addressed", False)),
            mitigation_notes=data.get("mitigation_notes"),
        )


@dataclass
class DialecticSession:
    """Represents an active or completed dialectic consensus arbitration session."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    topic: str = ""
    proponent_agent_id: str = ""
    skeptic_agent_id: str = ""
    arbitrator_agent_id: str = ""
    phase: DialecticPhase = DialecticPhase.PROPOSAL

    # Phase Data Payloads
    thesis: Optional[dict[str, Any]] = None
    antithesis: Optional[dict[str, Any]] = None
    critiques: List[CritiquePoint] = field(default_factory=list)
    rebuttal: Optional[dict[str, Any]] = None
    synthesis: Optional[dict[str, Any]] = None

    # Verdict & Scoring
    verdict: Optional[VerdictType] = None
    confidence_score: float = 0.0
    required_amendments: List[str] = field(default_factory=list)

    # Telemetry
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    audit_trail: List[dict[str, Any]] = field(default_factory=list)

    def log_event(self, action: str, actor: str, details: dict[str, Any]) -> None:
        """Record an immutable audit step in session history."""
        self.updated_at = time.time()
        self.audit_trail.append(
            {
                "timestamp": self.updated_at,
                "action": action,
                "actor": actor,
                "details": details,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize dialectic session to dictionary."""
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "proponent_agent_id": self.proponent_agent_id,
            "skeptic_agent_id": self.skeptic_agent_id,
            "arbitrator_agent_id": self.arbitrator_agent_id,
            "phase": self.phase.value,
            "thesis": self.thesis,
            "antithesis": self.antithesis,
            "critiques": [c.to_dict() for c in self.critiques],
            "rebuttal": self.rebuttal,
            "synthesis": self.synthesis,
            "verdict": self.verdict.value if self.verdict else None,
            "confidence_score": round(self.confidence_score, 4),
            "required_amendments": self.required_amendments,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "audit_trail": self.audit_trail,
        }


@dataclass
class ConsensusReport:
    """Final comprehensive summary report of a completed dialectic session."""

    session_id: str
    topic: str
    verdict: VerdictType
    confidence_score: float
    proponent_id: str
    skeptic_id: str
    arbitrator_id: str
    thesis_summary: str
    antithesis_summary: str
    critiques_count: int
    unaddressed_critiques_count: int
    required_amendments: List[str]
    arbitrator_synthesis: str
    timestamp: float = field(default_factory=time.time)

    def to_markdown(self) -> str:
        """Render consensus report as clean GitHub-flavored markdown."""
        amendments_md = (
            "\n".join([f"- {am}" for am in self.required_amendments])
            if self.required_amendments
            else "_None required._"
        )

        badge_color = {
            VerdictType.APPROVED: "🟩 APPROVED",
            VerdictType.AMENDED: "🟨 AMENDED",
            VerdictType.REJECTED: "🟥 REJECTED",
            VerdictType.ESCALATE: "🟪 ESCALATE (Human Attention Required)",
        }.get(self.verdict, str(self.verdict.value))

        return f"""# 🏛️ Dialectic Consensus Arbitration Report

**Session ID**: `{self.session_id}`  
**Topic**: **{self.topic}**  
**Final Verdict**: **{badge_color}**  
**Confidence Score**: **{self.confidence_score * 100:.1f}%** (`{self.confidence_score:.3f}`)  
**Date**: `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(self.timestamp))}`

---

### 👥 Participating Spirits
- **Proponent (Thesis)**: `{self.proponent_id}`
- **Skeptic (Antithesis)**: `{self.skeptic_id}`
- **Arbitrator (Synthesis)**: `{self.arbitrator_id}`

---

### 📜 1. Thesis (Proposal)
{self.thesis_summary}

---

### 🔍 2. Antithesis (Skeptic Critique)
{self.antithesis_summary}

- **Total Critiques**: {self.critiques_count}
- **Unaddressed High/Critical Risks**: {self.unaddressed_critiques_count}

---

### ⚖️ 3. Arbitrator Synthesis & Rationale
{self.arbitrator_synthesis}

---

### 🛠️ 4. Required Amendments & Conditions
{amendments_md}
"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConsensusEngine:
    """Engine managing 3-Way Dialectic Consensus sessions, validation, and scoring."""

    def __init__(self) -> None:
        self._sessions: Dict[str, DialecticSession] = {}

    def create_session(
        self,
        topic: str,
        proponent_id: str,
        skeptic_id: str,
        arbitrator_id: str,
    ) -> DialecticSession:
        """Initialize a new dialectic arbitration session."""
        session = DialecticSession(
            topic=topic,
            proponent_agent_id=proponent_id,
            skeptic_agent_id=skeptic_id,
            arbitrator_agent_id=arbitrator_id,
            phase=DialecticPhase.PROPOSAL,
        )
        session.log_event(
            action="session_created",
            actor="consensus_engine",
            details={
                "topic": topic,
                "proponent": proponent_id,
                "skeptic": skeptic_id,
                "arbitrator": arbitrator_id,
            },
        )
        self._sessions[session.session_id] = session
        logger.info(f"Created Dialectic Session '{session.session_id}' on topic: '{topic}'")
        return session

    def submit_thesis(
        self,
        session_id: str,
        agent_id: str,
        summary: str,
        proposal_details: dict[str, Any],
        justification: str = "",
    ) -> DialecticSession:
        """Phase 1: Proponent submits Thesis proposal."""
        session = self._get_session_or_raise(session_id)
        if session.phase != DialecticPhase.PROPOSAL:
            raise ValueError(f"Session is in phase '{session.phase.value}', expected '{DialecticPhase.PROPOSAL.value}'")

        if agent_id != session.proponent_agent_id and session.proponent_agent_id:
            raise PermissionError(f"Agent '{agent_id}' is not the designated Proponent ('{session.proponent_agent_id}')")

        session.thesis = {
            "summary": summary,
            "proposal_details": proposal_details,
            "justification": justification,
            "submitted_at": time.time(),
        }
        session.phase = DialecticPhase.CRITIQUE
        session.log_event("thesis_submitted", agent_id, {"summary": summary})
        logger.info(f"Session '{session_id}': Thesis submitted by '{agent_id}'")
        return session

    def submit_antithesis(
        self,
        session_id: str,
        agent_id: str,
        critiques: Optional[List[Any]] = None,
        summary: str = "",
        counter_arguments: str = "",
        risk_level: str = "medium",
    ) -> DialecticSession:
        """Phase 2: Skeptic submits Antithesis with critique points and risks."""
        session = self._get_session_or_raise(session_id)
        if session.phase != DialecticPhase.CRITIQUE:
            raise ValueError(f"Session is in phase '{session.phase.value}', expected '{DialecticPhase.CRITIQUE.value}'")

        if agent_id != session.skeptic_agent_id and session.skeptic_agent_id:
            raise PermissionError(f"Agent '{agent_id}' is not the designated Skeptic ('{session.skeptic_agent_id}')")

        critique_objs: List[CritiquePoint] = []
        for c in (critiques or []):
            critique_objs.append(CritiquePoint.from_dict(c) if isinstance(c, dict) else c)

        session.critiques = critique_objs
        session.antithesis = {
            "summary": summary,
            "counter_arguments": counter_arguments or summary,
            "risk_level": risk_level,
            "critique_count": len(critique_objs),
            "submitted_at": time.time(),
        }
        session.phase = DialecticPhase.REBUTTAL
        session.log_event(
            "antithesis_submitted",
            agent_id,
            {"critiques_count": len(critique_objs), "risk_level": risk_level, "summary": summary},
        )
        logger.info(f"Session '{session_id}': Antithesis submitted by '{agent_id}' with {len(critique_objs)} critiques")
        return session

    def submit_rebuttal(
        self,
        session_id: str,
        agent_id: str,
        mitigations: Optional[List[dict[str, Any]]] = None,
        concessions: Optional[List[str]] = None,
        comments: str = "",
        addressed_critiques: Optional[List[str]] = None,
        rebuttals: Optional[dict[str, Any]] = None,
    ) -> DialecticSession:
        """Phase 3: Proponent responds with mitigations for specific critiques."""
        session = self._get_session_or_raise(session_id)
        if session.phase != DialecticPhase.REBUTTAL:
            raise ValueError(f"Session is in phase '{session.phase.value}', expected '{DialecticPhase.REBUTTAL.value}'")

        if agent_id != session.proponent_agent_id and session.proponent_agent_id:
            raise PermissionError(f"Agent '{agent_id}' is not the designated Proponent ('{session.proponent_agent_id}')")

        # Mark addressed critiques from mitigations list
        mitigation_map = {
            m.get("critique_id"): m.get("notes")
            for m in (mitigations or [])
            if isinstance(m, dict) and m.get("critique_id")
        }
        for critique in session.critiques:
            if critique.id in mitigation_map:
                critique.is_addressed = True
                critique.mitigation_notes = mitigation_map[critique.id]
            elif addressed_critiques and critique.id in addressed_critiques:
                critique.is_addressed = True
                if rebuttals:
                    critique.mitigation_notes = str(rebuttals)

        session.rebuttal = {
            "mitigations": mitigations or [],
            "concessions": concessions or [],
            "comments": comments,
            "addressed_critiques": addressed_critiques or [],
            "rebuttals": rebuttals or {},
            "submitted_at": time.time(),
        }
        session.phase = DialecticPhase.SYNTHESIS
        session.log_event(
            "rebuttal_submitted",
            agent_id,
            {"mitigations_count": len(mitigations or []) + len(addressed_critiques or [])},
        )
        logger.info(f"Session '{session_id}': Rebuttal submitted by '{agent_id}'")
        return session

    def synthesize(
        self,
        session_id: str,
        agent_id: str,
        arbitrator_notes: str = "",
        rationale: str = "",
        required_amendments: Optional[List[str]] = None,
        force_verdict: Optional[VerdictType] = None,
    ) -> ConsensusReport:
        """Phase 4: Arbitrator generates synthesis, calculates confidence score, and delivers verdict."""
        session = self._get_session_or_raise(session_id)
        if session.phase not in (DialecticPhase.SYNTHESIS, DialecticPhase.REBUTTAL):
            raise ValueError(f"Session is in phase '{session.phase.value}', cannot synthesize")

        if agent_id != session.arbitrator_agent_id and session.arbitrator_agent_id:
            raise PermissionError(f"Agent '{agent_id}' is not the designated Arbitrator ('{session.arbitrator_agent_id}')")

        notes = arbitrator_notes or rationale

        # Calculate confidence score
        confidence = self._compute_confidence_score(session)
        session.confidence_score = confidence

        amendments = required_amendments or []
        session.required_amendments = amendments

        # Determine automatic verdict if not forced
        if force_verdict:
            verdict = force_verdict
        else:
            verdict = self._determine_verdict(session, confidence)

        session.verdict = verdict
        session.synthesis = {
            "arbitrator_notes": notes,
            "confidence_score": confidence,
            "verdict": verdict.value,
            "amendments": amendments,
            "synthesized_at": time.time(),
        }
        session.phase = DialecticPhase.FINALIZED
        session.log_event(
            "synthesis_completed",
            agent_id,
            {"verdict": verdict.value, "confidence_score": confidence},
        )

        thesis_text = session.thesis.get("summary", "") if session.thesis else ""
        antithesis_text = (
            session.antithesis.get("counter_arguments")
            or session.antithesis.get("summary")
            or ""
        ) if session.antithesis else ""
        unaddressed = sum(
            1
            for c in session.critiques
            if not c.is_addressed and c.severity in (CritiqueSeverity.CRITICAL, CritiqueSeverity.HIGH)
        )

        report = ConsensusReport(
            session_id=session.session_id,
            topic=session.topic,
            verdict=verdict,
            confidence_score=confidence,
            proponent_id=session.proponent_agent_id,
            skeptic_id=session.skeptic_agent_id,
            arbitrator_id=session.arbitrator_agent_id,
            thesis_summary=thesis_text,
            antithesis_summary=antithesis_text,
            critiques_count=len(session.critiques),
            unaddressed_critiques_count=unaddressed,
            required_amendments=amendments,
            arbitrator_synthesis=notes,
        )

        logger.info(
            f"Consensus reached for session '{session_id}': Verdict={verdict.value}, Confidence={confidence:.3f}"
        )
        return report

    def _compute_confidence_score(self, session: DialecticSession) -> float:
        """Compute weighted consensus confidence score (0.0 to 1.0)."""
        base_score = 1.0
        total_penalty = 0.0

        for critique in session.critiques:
            weight = SEVERITY_WEIGHTS.get(critique.severity, 0.10)
            if critique.is_addressed:
                # Addressed critique reduces penalty by 85%
                total_penalty += weight * 0.15
            else:
                total_penalty += weight

        calculated_score = max(0.0, min(1.0, base_score - total_penalty))
        return calculated_score

    def _determine_verdict(self, session: DialecticSession, confidence: float) -> VerdictType:
        """Classify final verdict from confidence score and remaining critical flaws."""
        critical_unaddressed = any(
            c.severity == CritiqueSeverity.CRITICAL and not c.is_addressed
            for c in session.critiques
        )

        if critical_unaddressed:
            return VerdictType.REJECTED

        if confidence >= 0.85:
            return VerdictType.APPROVED
        elif confidence >= 0.60:
            return VerdictType.AMENDED
        elif confidence >= 0.40:
            return VerdictType.ESCALATE
        else:
            return VerdictType.REJECTED

    def get_session(self, session_id: str) -> Optional[DialecticSession]:
        """Retrieve an active or archived dialectic session."""
        return self._sessions.get(session_id)

    def _get_session_or_raise(self, session_id: str) -> DialecticSession:
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Dialectic session '{session_id}' does not exist.")
        return session
