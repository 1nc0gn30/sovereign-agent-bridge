"""Tests for Heartbeat Watchdog and Dead-Man Switch in sovereign_agent_bridge.watchdog."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import pytest

from sovereign_agent_bridge.watchdog import (
    AgentPulse,
    AgentStatus,
    AlertSeverity,
    DeadManSwitchAlert,
    HeartbeatWatchdog,
    check_watchdog_liveness,
    get_default_watchdog,
    record_pulse,
)


def test_agent_pulse_serialization():
    """Test AgentPulse dataclass serialization and timing properties."""
    pulse = AgentPulse(
        agent_id="agent-pulse-01",
        interval=30.0,
        timeout=75.0,
        metadata={"node": "sovereign-alpha"},
    )

    assert pulse.agent_id == "agent-pulse-01"
    assert pulse.status == AgentStatus.ACTIVE
    assert pulse.elapsed_seconds >= 0.0
    assert pulse.time_to_timeout > 0.0

    d = pulse.to_dict()
    assert d["agent_id"] == "agent-pulse-01"
    assert d["interval"] == 30.0

    restored = AgentPulse.from_dict(d)
    assert restored.agent_id == pulse.agent_id
    assert restored.timeout == pulse.timeout


def test_watchdog_record_pulse_and_liveness_transition(tmp_path: Path):
    """Test heartbeat recording, WARNING detection, DEAD transition, and recovery."""
    storage = tmp_path / "heartbeats.json"
    alerts_log = tmp_path / "alerts.jsonl"
    wd = HeartbeatWatchdog(
        check_interval=0.5,
        storage_path=storage,
        alert_log_path=alerts_log,
        auto_save=True,
    )

    t0 = 1000.0
    # 1. Record initial pulse
    pulse = wd.record_pulse("agent-001", interval=10.0, timeout=25.0)
    pulse.last_pulse = t0

    # 2. Check at t0 + 5s (still ACTIVE)
    alerts = wd.check_liveness(now=t0 + 5.0)
    assert len(alerts) == 0
    assert wd.get_agent("agent-001").status == AgentStatus.ACTIVE

    # 3. Check at t0 + 16s (exceeds 1.5x interval = 15s -> WARNING)
    alerts = wd.check_liveness(now=t0 + 16.0)
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.WARNING
    assert wd.get_agent("agent-001").status == AgentStatus.WARNING

    # 4. Check at t0 + 26s (exceeds timeout = 25s -> CRITICAL DEAD)
    alerts = wd.check_liveness(now=t0 + 26.0)
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.CRITICAL
    assert alerts[0].status == AgentStatus.DEAD
    assert wd.get_agent("agent-001").status == AgentStatus.DEAD

    # 5. Agent sends pulse after dead -> RECOVERED
    recovery_alerts: List[DeadManSwitchAlert] = []
    wd.add_alert_handler(lambda a: recovery_alerts.append(a))

    recovered_pulse = wd.record_pulse("agent-001", interval=10.0, timeout=25.0)
    assert recovered_pulse.status == AgentStatus.ACTIVE
    assert any(a.severity == AlertSeverity.RESOLVED for a in recovery_alerts)


def test_watchdog_persistence_and_telemetry(tmp_path: Path):
    """Test saving and loading watchdog state to disk."""
    storage = tmp_path / "persisted_heartbeats.json"
    wd = HeartbeatWatchdog(storage_path=storage, auto_save=True)

    wd.record_pulse("agent-persisted", interval=60.0, metadata={"cluster": "test"})
    assert storage.exists()

    # Create new instance and verify loaded state
    wd_loaded = HeartbeatWatchdog(storage_path=storage)
    assert wd_loaded.get_agent("agent-persisted") is not None
    assert wd_loaded.get_agent("agent-persisted").metadata["cluster"] == "test"

    # Verify telemetry structure
    telemetry = wd_loaded.get_telemetry()
    assert telemetry["total_monitored_agents"] == 1
    assert "uptime_seconds" in telemetry


def test_watchdog_thread_lifecycle(tmp_path: Path):
    """Test background watchdog thread start, run, and stop."""
    wd = HeartbeatWatchdog(check_interval=0.1, storage_path=tmp_path / "th.json")
    wd.start()
    assert wd.is_running() is True
    time.sleep(0.2)
    wd.stop()
    assert wd.is_running() is False


def test_global_watchdog_helpers():
    """Test top-level watchdog singleton and convenience functions."""
    p = record_pulse("agent-global-test", interval=45.0)
    assert p.agent_id == "agent-global-test"

    wd = get_default_watchdog()
    assert wd.get_agent("agent-global-test") is not None
