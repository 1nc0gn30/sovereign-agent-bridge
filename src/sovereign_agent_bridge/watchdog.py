"""
Heartbeat Watchdog and Dead-Man Switch Alerting System for Sovereign Agent Bridge.

Monitors autonomous agent liveness, tracks heartbeat pulse timestamps, detects
silent process failures or network partitions, and dispatches automated alerts
via local event logs, webhooks, and registered multi-channel bridge dispatchers.
Zero external dependencies (pure Python standard library).
"""

from __future__ import annotations

import os
import sys
import time
import json
import uuid
import socket
import logging
import threading
import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Union
import urllib.request
import urllib.error

logger = logging.getLogger("sovereign_agent_bridge.watchdog")


class AgentStatus:
    """Agent liveness lifecycle status constants."""
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    DEAD = "DEAD"
    RECOVERED = "RECOVERED"
    UNKNOWN = "UNKNOWN"


class AlertSeverity:
    """Dead-man switch alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    RESOLVED = "RESOLVED"


@dataclasses.dataclass
class AgentPulse:
    """Tracks state and timing parameters for a monitored sovereign agent."""
    agent_id: str
    last_pulse: float = dataclasses.field(default_factory=time.time)
    interval: float = 60.0  # Expected heartbeat pulse interval (seconds)
    timeout: float = 150.0  # Timeout threshold before declared DEAD (default: 2.5x interval)
    status: str = AgentStatus.ACTIVE
    missed_pulses: int = 0
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    first_seen: float = dataclasses.field(default_factory=time.time)
    total_pulses: int = 1
    last_alert_time: float = 0.0
    alert_dispatched: bool = False

    def __post_init__(self):
        if self.timeout is None or self.timeout <= 0:
            self.timeout = max(self.interval * 2.5, 10.0)

    @property
    def elapsed_seconds(self) -> float:
        """Seconds elapsed since last received heartbeat pulse."""
        return max(0.0, time.time() - self.last_pulse)

    @property
    def time_to_timeout(self) -> float:
        """Seconds remaining before dead-man switch triggers."""
        return max(0.0, self.timeout - self.elapsed_seconds)

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Returns True if the pulse has exceeded its timeout threshold."""
        current_time = now if now is not None else time.time()
        return (current_time - self.last_pulse) > self.timeout

    def is_warning(self, now: Optional[float] = None) -> bool:
        """Returns True if pulse has exceeded 1.5x expected interval but not yet timed out."""
        current_time = now if now is not None else time.time()
        elapsed = current_time - self.last_pulse
        return elapsed > (self.interval * 1.5) and not self.is_expired(current_time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize pulse state to dictionary."""
        return {
            "agent_id": self.agent_id,
            "last_pulse": self.last_pulse,
            "last_pulse_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_pulse)),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "interval": self.interval,
            "timeout": self.timeout,
            "time_to_timeout": round(self.time_to_timeout, 2),
            "status": self.status,
            "missed_pulses": self.missed_pulses,
            "first_seen": self.first_seen,
            "total_pulses": self.total_pulses,
            "alert_dispatched": self.alert_dispatched,
            "last_alert_time": self.last_alert_time,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentPulse:
        """Deserialize pulse state from dictionary."""
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclasses.dataclass
class DeadManSwitchAlert:
    """Event record generated when an agent fails to report or recovers."""
    alert_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    timestamp: float = dataclasses.field(default_factory=time.time)
    last_pulse: float = 0.0
    elapsed_seconds: float = 0.0
    timeout_threshold: float = 0.0
    severity: str = AlertSeverity.CRITICAL
    status: str = AgentStatus.DEAD
    reason: str = ""
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    hostname: str = dataclasses.field(default_factory=socket.gethostname)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize alert to dictionary."""
        return {
            "alert_id": self.alert_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)),
            "last_pulse": self.last_pulse,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "timeout_threshold": self.timeout_threshold,
            "severity": self.severity,
            "status": self.status,
            "reason": self.reason,
            "metadata": self.metadata,
            "hostname": self.hostname,
        }


class HeartbeatWatchdog:
    """
    Heartbeat Watchdog and Dead-Man Switch Alerting Engine.
    
    Provides thread-safe agent pulse registration, background monitoring,
    liveness inspection, and multi-channel alert dispatching.
    """

    def __init__(
        self,
        check_interval: float = 5.0,
        storage_path: Optional[Union[str, Path]] = None,
        alert_log_path: Optional[Union[str, Path]] = None,
        webhook_url: Optional[str] = None,
        auto_save: bool = True,
    ):
        self.check_interval = max(0.5, float(check_interval))
        self.auto_save = auto_save
        self.webhook_url = webhook_url or os.environ.get("WATCHDOG_WEBHOOK_URL")

        # Resolve storage directory
        default_dir = Path.home() / ".sovereign_bridge"
        try:
            default_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            default_dir = Path("./.sovereign_bridge")
            default_dir.mkdir(parents=True, exist_ok=True)

        self.storage_path = Path(storage_path) if storage_path else default_dir / "heartbeats.json"
        self.alert_log_path = Path(alert_log_path) if alert_log_path else default_dir / "watchdog_alerts.jsonl"

        self._pulses: Dict[str, AgentPulse] = {}
        self._lock = threading.RLock()
        self._running = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._alert_handlers: List[Callable[[DeadManSwitchAlert], None]] = []
        self._alerts_history: List[DeadManSwitchAlert] = []
        self._max_alerts_history = 200
        self._start_time = time.time()
        self._total_pulses_recorded = 0
        self._last_check_timestamp = 0.0

        # Load existing state if present
        if self.storage_path.exists():
            self.load_state()

    def record_pulse(
        self,
        agent_id: str,
        interval: float = 60.0,
        timeout: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentPulse:
        """
        Record a heartbeat pulse from an autonomous agent.
        
        If the agent was previously in WARNING or DEAD state, this records
        a recovery event and dispatches a RESOLVED alert.
        """
        agent_id = str(agent_id).strip()
        if not agent_id:
            raise ValueError("agent_id cannot be empty")

        now = time.time()
        calc_timeout = timeout if timeout is not None else max(interval * 2.5, 10.0)
        meta = metadata or {}

        recovered_alert: Optional[DeadManSwitchAlert] = None

        with self._lock:
            self._total_pulses_recorded += 1
            if agent_id in self._pulses:
                pulse = self._pulses[agent_id]
                was_dead_or_warning = pulse.status in (AgentStatus.DEAD, AgentStatus.WARNING)
                previous_status = pulse.status
                elapsed = now - pulse.last_pulse

                pulse.last_pulse = now
                pulse.interval = interval
                pulse.timeout = calc_timeout
                pulse.total_pulses += 1
                pulse.missed_pulses = 0
                pulse.metadata.update(meta)

                if was_dead_or_warning:
                    pulse.status = AgentStatus.RECOVERED
                    pulse.alert_dispatched = False
                    recovered_alert = DeadManSwitchAlert(
                        agent_id=agent_id,
                        timestamp=now,
                        last_pulse=now,
                        elapsed_seconds=elapsed,
                        timeout_threshold=calc_timeout,
                        severity=AlertSeverity.RESOLVED,
                        status=AgentStatus.RECOVERED,
                        reason=f"Agent '{agent_id}' recovered after {elapsed:.1f}s of silence (was {previous_status}).",
                        metadata=pulse.metadata,
                    )
                    # Quickly transition back to ACTIVE on subsequent cycle
                    pulse.status = AgentStatus.ACTIVE
                else:
                    pulse.status = AgentStatus.ACTIVE
            else:
                pulse = AgentPulse(
                    agent_id=agent_id,
                    last_pulse=now,
                    interval=interval,
                    timeout=calc_timeout,
                    status=AgentStatus.ACTIVE,
                    missed_pulses=0,
                    metadata=meta,
                    first_seen=now,
                    total_pulses=1,
                    last_alert_time=0.0,
                    alert_dispatched=False,
                )
                self._pulses[agent_id] = pulse

            if self.auto_save:
                self._save_state_locked()

        if recovered_alert:
            self._dispatch_alert(recovered_alert)

        return pulse

    def get_agent(self, agent_id: str) -> Optional[AgentPulse]:
        """Get live pulse state object for a specific agent."""
        with self._lock:
            return self._pulses.get(agent_id)

    def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get dictionary snapshot of a specific agent's status."""
        with self._lock:
            pulse = self._pulses.get(agent_id)
            return pulse.to_dict() if pulse else None

    def list_agents(self) -> List[AgentPulse]:
        """List all registered agent pulses."""
        with self._lock:
            return list(self._pulses.values())

    def list_agents_dict(self) -> List[Dict[str, Any]]:
        """List all registered agents serialized to dictionaries."""
        with self._lock:
            return [p.to_dict() for p in self._pulses.values()]

    def unregister_agent(self, agent_id: str) -> bool:
        """Unregister an agent from watchdog monitoring."""
        with self._lock:
            if agent_id in self._pulses:
                del self._pulses[agent_id]
                if self.auto_save:
                    self._save_state_locked()
                return True
            return False

    def add_alert_handler(self, handler: Callable[[DeadManSwitchAlert], None]) -> None:
        """Register a callback handler for dead-man switch alerts."""
        with self._lock:
            if handler not in self._alert_handlers:
                self._alert_handlers.append(handler)

    def remove_alert_handler(self, handler: Callable[[DeadManSwitchAlert], None]) -> None:
        """Unregister a callback handler."""
        with self._lock:
            if handler in self._alert_handlers:
                self._alert_handlers.remove(handler)

    def check_liveness(self, now: Optional[float] = None) -> List[DeadManSwitchAlert]:
        """
        Scan all monitored agents, evaluate pulse timestamps, and trigger alerts
        for any agent exceeding warning or timeout thresholds.
        """
        current_time = now if now is not None else time.time()
        new_alerts: List[DeadManSwitchAlert] = []
        state_changed = False

        with self._lock:
            self._last_check_timestamp = current_time
            for agent_id, pulse in self._pulses.items():
                elapsed = current_time - pulse.last_pulse

                # 1. Critical Dead-Man Switch Timeout
                if elapsed > pulse.timeout:
                    if pulse.status != AgentStatus.DEAD or not pulse.alert_dispatched:
                        pulse.status = AgentStatus.DEAD
                        pulse.missed_pulses = max(1, int(elapsed // max(1.0, pulse.interval)))
                        pulse.alert_dispatched = True
                        pulse.last_alert_time = current_time
                        state_changed = True

                        alert = DeadManSwitchAlert(
                            agent_id=agent_id,
                            timestamp=current_time,
                            last_pulse=pulse.last_pulse,
                            elapsed_seconds=elapsed,
                            timeout_threshold=pulse.timeout,
                            severity=AlertSeverity.CRITICAL,
                            status=AgentStatus.DEAD,
                            reason=(
                                f"DEAD-MAN SWITCH TRIGGERED: Agent '{agent_id}' has been silent for "
                                f"{elapsed:.1f}s (timeout threshold: {pulse.timeout:.1f}s, "
                                f"estimated {pulse.missed_pulses} missed pulses)."
                            ),
                            metadata=pulse.metadata,
                        )
                        new_alerts.append(alert)

                # 2. Warning Threshold (elapsed > 1.5x interval)
                elif elapsed > (pulse.interval * 1.5):
                    if pulse.status == AgentStatus.ACTIVE:
                        pulse.status = AgentStatus.WARNING
                        pulse.missed_pulses = max(1, int(elapsed // max(1.0, pulse.interval)))
                        state_changed = True

                        alert = DeadManSwitchAlert(
                            agent_id=agent_id,
                            timestamp=current_time,
                            last_pulse=pulse.last_pulse,
                            elapsed_seconds=elapsed,
                            timeout_threshold=pulse.timeout,
                            severity=AlertSeverity.WARNING,
                            status=AgentStatus.WARNING,
                            reason=(
                                f"HEARTBEAT WARNING: Agent '{agent_id}' missed expected pulse interval "
                                f"({elapsed:.1f}s elapsed > {pulse.interval * 1.5:.1f}s warning threshold)."
                            ),
                            metadata=pulse.metadata,
                        )
                        new_alerts.append(alert)

            if state_changed and self.auto_save:
                self._save_state_locked()

        # Dispatch alerts outside lock
        for alert in new_alerts:
            self._dispatch_alert(alert)

        return new_alerts

    def _dispatch_alert(self, alert: DeadManSwitchAlert) -> None:
        """Dispatch alert to callbacks, JSONL log file, stderr, and webhook."""
        with self._lock:
            self._alerts_history.append(alert)
            if len(self._alerts_history) > self._max_alerts_history:
                self._alerts_history.pop(0)

        # Log alert
        if alert.severity == AlertSeverity.CRITICAL:
            logger.critical("[%s] %s", alert.agent_id, alert.reason)
        elif alert.severity == AlertSeverity.WARNING:
            logger.warning("[%s] %s", alert.agent_id, alert.reason)
        else:
            logger.info("[%s] %s", alert.agent_id, alert.reason)

        # Append to JSONL log file
        try:
            self.alert_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.alert_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert.to_dict()) + "\n")
        except Exception as e:
            logger.error("Failed to append alert to %s: %s", self.alert_log_path, e)

        # Trigger registered handlers
        for handler in list(self._alert_handlers):
            try:
                handler(alert)
            except Exception as e:
                logger.error("Error executing alert handler %s: %s", handler, e)

        # Dispatch webhook if configured
        if self.webhook_url:
            self._dispatch_webhook(alert)

    def _dispatch_webhook(self, alert: DeadManSwitchAlert) -> None:
        """Asynchronously send HTTP POST webhook notification."""
        def _send():
            try:
                data = json.dumps(alert.to_dict()).encode("utf-8")
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "SovereignAgentBridge-Watchdog/0.1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    logger.debug("Webhook response code: %d", resp.status)
            except Exception as err:
                logger.warning("Failed to dispatch alert webhook to %s: %s", self.webhook_url, err)

        threading.Thread(target=_send, name="Watchdog-Webhook", daemon=True).start()

    def _monitor_loop(self) -> None:
        """Background monitoring loop running on dedicated daemon thread."""
        logger.info("Watchdog monitor thread started (interval=%.1fs)", self.check_interval)
        while not self._stop_event.is_set():
            try:
                self.check_liveness()
            except Exception as e:
                logger.error("Unexpected error in watchdog check_liveness: %s", e)
            self._stop_event.wait(self.check_interval)
        logger.info("Watchdog monitor thread exited")

    def start(self) -> HeartbeatWatchdog:
        """Start the background watchdog monitoring thread."""
        with self._lock:
            if self._running:
                return self
            self._running = True
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="SovereignWatchdogMonitor",
                daemon=True,
            )
            self._monitor_thread.start()
            return self

    def stop(self) -> None:
        """Stop background monitoring thread."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3.0)

        if self.auto_save:
            self.save_state()

    def is_running(self) -> bool:
        """Returns True if watchdog thread is actively running."""
        return self._running and (self._monitor_thread is not None and self._monitor_thread.is_alive())

    def join(self, timeout: Optional[float] = None) -> None:
        """Block until monitoring thread terminates."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=timeout)

    def _save_state_locked(self) -> None:
        """Internal atomic state saver (assumes _lock is held)."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.storage_path.with_suffix(".tmp")
            data = {
                "version": "0.1.0",
                "saved_at": time.time(),
                "saved_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_pulses_recorded": self._total_pulses_recorded,
                "agents": {aid: p.to_dict() for aid, p in self._pulses.items()},
            }
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_path.replace(self.storage_path)
        except Exception as e:
            logger.error("Failed to save watchdog state to %s: %s", self.storage_path, e)

    def save_state(self, path: Optional[Union[str, Path]] = None) -> bool:
        """Persist current heartbeat state to disk."""
        target = Path(path) if path else self.storage_path
        with self._lock:
            old_path = self.storage_path
            self.storage_path = target
            try:
                self._save_state_locked()
                return True
            finally:
                self.storage_path = old_path

    def load_state(self, path: Optional[Union[str, Path]] = None) -> bool:
        """Load persisted heartbeat state from disk."""
        target = Path(path) if path else self.storage_path
        if not target.exists():
            return False

        with self._lock:
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                agents_data = data.get("agents", {})
                self._pulses = {
                    aid: AgentPulse.from_dict(pdata)
                    for aid, pdata in agents_data.items()
                }
                self._total_pulses_recorded = data.get("total_pulses_recorded", len(self._pulses))
                return True
            except Exception as e:
                logger.error("Failed to load watchdog state from %s: %s", target, e)
                return False

    def get_telemetry(self) -> Dict[str, Any]:
        """Return comprehensive watchdog runtime telemetry."""
        with self._lock:
            now = time.time()
            active_count = sum(1 for p in self._pulses.values() if p.status == AgentStatus.ACTIVE)
            warning_count = sum(1 for p in self._pulses.values() if p.status == AgentStatus.WARNING)
            dead_count = sum(1 for p in self._pulses.values() if p.status == AgentStatus.DEAD)
            recovered_count = sum(1 for p in self._pulses.values() if p.status == AgentStatus.RECOVERED)

            return {
                "is_running": self.is_running(),
                "uptime_seconds": round(now - self._start_time, 2),
                "check_interval": self.check_interval,
                "total_monitored_agents": len(self._pulses),
                "active_agents": active_count,
                "warning_agents": warning_count,
                "dead_agents": dead_count,
                "recovered_agents": recovered_count,
                "total_pulses_recorded": self._total_pulses_recorded,
                "total_alerts_generated": len(self._alerts_history),
                "last_check_timestamp": self._last_check_timestamp,
                "storage_path": str(self.storage_path),
                "alert_log_path": str(self.alert_log_path),
                "webhook_configured": bool(self.webhook_url),
            }

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return most recent alert records."""
        with self._lock:
            return [a.to_dict() for a in self._alerts_history[-limit:]]

    def reset(self) -> None:
        """Reset all in-memory pulses and alerts."""
        with self._lock:
            self._pulses.clear()
            self._alerts_history.clear()
            self._total_pulses_recorded = 0
            if self.auto_save:
                self._save_state_locked()

    def __enter__(self) -> HeartbeatWatchdog:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Global Singleton & Top-Level API
# ---------------------------------------------------------------------------

_global_watchdog: Optional[HeartbeatWatchdog] = None
_global_lock = threading.Lock()


def get_default_watchdog() -> HeartbeatWatchdog:
    """Retrieve or initialize the global shared HeartbeatWatchdog instance."""
    global _global_watchdog
    with _global_lock:
        if _global_watchdog is None:
            _global_watchdog = HeartbeatWatchdog()
        return _global_watchdog


def record_pulse(
    agent_id: str,
    interval: float = 60.0,
    timeout: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentPulse:
    """Record an agent heartbeat pulse using the default watchdog."""
    return get_default_watchdog().record_pulse(
        agent_id=agent_id,
        interval=interval,
        timeout=timeout,
        metadata=metadata,
    )


def check_watchdog_liveness() -> List[DeadManSwitchAlert]:
    """Execute immediate liveness sweep on the default watchdog."""
    return get_default_watchdog().check_liveness()


# ---------------------------------------------------------------------------
# Standalone Execution Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s",
    )
    print("Starting Sovereign Agent Bridge Watchdog in standalone mode...")
    wd = HeartbeatWatchdog(check_interval=2.0)
    wd.start()

    # Record self pulse
    wd.record_pulse("watchdog-local", interval=5.0, metadata={"role": "monitor"})

    try:
        while True:
            time.sleep(1.0)
            telemetry = wd.get_telemetry()
            # print status pulse
            sys.stdout.write(
                f"\rAgents: {telemetry['total_monitored_agents']} | "
                f"Active: {telemetry['active_agents']} | "
                f"Dead: {telemetry['dead_agents']} | "
                f"Pulses: {telemetry['total_pulses_recorded']} | "
                f"Uptime: {telemetry['uptime_seconds']}s"
            )
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nStopping watchdog...")
        wd.stop()
        print("Watchdog stopped cleanly.")
