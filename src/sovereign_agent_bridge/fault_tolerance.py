"""Fault-Tolerance Mesh: Adaptive Circuit Breaker, Exponential Jitter Retry, and Anti-Replay Guard.

Provides distributed reliability and zero-trust security for sovereign multi-agent communications:
1. Adaptive Circuit Breaker:
   - Tracks target channel and peer agent health across CLOSED, OPEN, and HALF_OPEN states.
   - Protects against cascading timeout spirals and resource exhaustion.
2. Exponential Backoff with Jitter Retry Engine:
   - Full jitter sleep calculation to prevent thundering-herd resonance across the swarm.
3. Anti-Replay & Cryptographic Nonce Guard:
   - Enforces sliding-window TTL freshness and detects duplicate delivery replay attacks.
   - Supports optional HMAC-SHA256 message integrity verification.

100% Python Standard Library. Zero external dependencies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import math
import os
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Circuit Breaker Implementation
# ---------------------------------------------------------------------------

class CircuitState:
    CLOSED = "CLOSED"      # Normal healthy operation
    OPEN = "OPEN"          # Failing, fast-fail without network call
    HALF_OPEN = "HALF_OPEN"  # Testing recovery with trial traffic


@dataclass
class CircuitMetrics:
    """Telemetry snapshot for a circuit breaker."""

    name: str
    state: str
    failure_count: int
    success_count: int
    consecutive_successes: int
    last_failure_time: Optional[float]
    last_state_change: float
    total_calls: int
    rejected_calls: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CircuitBreaker:
    """Thread-safe adaptive circuit breaker for peer agents and communication channels."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
        half_open_success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.001, recovery_timeout)
        self.half_open_success_threshold = max(1, half_open_success_threshold)

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._consecutive_successes = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change = time.time()
        self._total_calls = 0
        self._rejected_calls = 0
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            # Check for automatic transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN and self._last_failure_time:
                if (time.time() - self._last_failure_time) >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._consecutive_successes = 0
                    self._last_state_change = time.time()
            return self._state

    def can_execute(self) -> bool:
        """Query whether a call is permitted through the circuit."""
        with self._lock:
            current_state = self.state
            if current_state == CircuitState.OPEN:
                self._rejected_calls += 1
                return False
            return True

    def record_success(self) -> None:
        """Record a successful execution, updating recovery state if HALF_OPEN."""
        with self._lock:
            self._total_calls += 1
            self._success_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.half_open_success_threshold:
                    # Circuit recovered
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._consecutive_successes = 0
                    self._last_state_change = time.time()
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self, error_msg: str = "") -> None:
        """Record an execution failure, potentially tripping the circuit to OPEN."""
        with self._lock:
            self._total_calls += 1
            self._failure_count += 1
            now = time.time()
            self._last_failure_time = now

            if self._state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
                if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = now

    def reset(self) -> None:
        """Manually force circuit reset to healthy CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._consecutive_successes = 0
            self._last_failure_time = None
            self._last_state_change = time.time()

    def get_metrics(self) -> CircuitMetrics:
        with self._lock:
            return CircuitMetrics(
                name=self.name,
                state=self.state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                consecutive_successes=self._consecutive_successes,
                last_failure_time=self._last_failure_time,
                last_state_change=self._last_state_change,
                total_calls=self._total_calls,
                rejected_calls=self._rejected_calls,
            )


class CircuitBreakerRegistry:
    """Central registry tracking circuit breakers across all channels and agents."""

    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
    ) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                )
            return self._breakers[name]

    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {name: cb.get_metrics().to_dict() for name, cb in self._breakers.items()}


# ---------------------------------------------------------------------------
# Retry Policy with Exponential Backoff and Full Jitter
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    """Exponential backoff retry policy with full random jitter."""

    max_retries: int = 3
    initial_interval: float = 0.05
    max_interval: float = 2.0
    multiplier: float = 2.0
    jitter: bool = True

    def calculate_delay(self, attempt: int) -> float:
        """Compute sleep duration for the given 0-indexed attempt."""
        calculated = self.initial_interval * (self.multiplier ** attempt)
        capped = min(self.max_interval, calculated)
        if self.jitter:
            return random.uniform(0.0, capped)
        return capped

    def execute(self, func: Callable[[], Any], fallback: Optional[Callable[[Exception], Any]] = None) -> Any:
        """Execute callable with automatic retry and exponential backoff."""
        last_exception: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return func()
            except Exception as ex:
                last_exception = ex
                if attempt < self.max_retries:
                    delay = self.calculate_delay(attempt)
                    time.sleep(delay)
                else:
                    break

        if fallback:
            return fallback(last_exception)  # type: ignore[arg-type]
        raise last_exception  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Anti-Replay and Nonce Guard
# ---------------------------------------------------------------------------

class AntiReplayGuard:
    """Zero-trust sliding-window nonce guard protecting against duplicate delivery and replay attacks."""

    def __init__(self, window_seconds: float = 300.0) -> None:
        self.window_seconds = max(1.0, window_seconds)
        self._seen_nonces: Dict[str, float] = {}
        self._lock = threading.RLock()

    def verify_and_record(
        self,
        nonce: str,
        timestamp: float,
        signature: Optional[str] = None,
        payload_bytes: Optional[bytes] = None,
        secret: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify message freshness and cryptographic uniqueness.
        
        Returns:
            (is_valid: bool, error_reason: Optional[str])
        """
        now = time.time()
        with self._lock:
            # 1. Clean expired nonces
            cutoff = now - self.window_seconds
            expired_keys = [k for k, ts in self._seen_nonces.items() if ts < cutoff]
            for k in expired_keys:
                del self._seen_nonces[k]

            # 2. Check time window freshness (allow slight clock drift of 15 seconds)
            if abs(now - timestamp) > (self.window_seconds + 15.0):
                return False, f"Message expired or timestamp drift too large (age: {now - timestamp:.2f}s, window: {self.window_seconds}s)"

            # 3. Check for nonce replay
            if nonce in self._seen_nonces:
                return False, f"Replay attack detected: nonce '{nonce}' has already been processed"

            # 4. Check HMAC signature if secret provided
            if secret and signature and payload_bytes:
                expected_hmac = hmac.new(
                    secret.encode("utf-8"),
                    f"{nonce}:{timestamp}:".encode("utf-8") + payload_bytes,
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(signature, expected_hmac):
                    return False, "Cryptographic signature mismatch"

            # 5. Record nonce
            self._seen_nonces[nonce] = now
            return True, None

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active_nonces_tracked": len(self._seen_nonces),
                "window_seconds": self.window_seconds,
            }


# ---------------------------------------------------------------------------
# Global Singletons and Dispatch Wrapper
# ---------------------------------------------------------------------------

_GLOBAL_CIRCUIT_REGISTRY = CircuitBreakerRegistry()
_GLOBAL_ANTI_REPLAY = AntiReplayGuard()


def get_circuit_registry() -> CircuitBreakerRegistry:
    return _GLOBAL_CIRCUIT_REGISTRY


def get_anti_replay_guard() -> AntiReplayGuard:
    return _GLOBAL_ANTI_REPLAY


def execute_resilient_channel_dispatch(
    channel_name: str,
    dispatch_fn: Callable[[], Any],
    fallback_fn: Optional[Callable[[], Any]] = None,
    retry_policy: Optional[RetryPolicy] = None,
) -> Dict[str, Any]:
    """
    Executes a channel dispatch protected by Circuit Breaker and Exponential Jitter Retries.
    """
    breaker = _GLOBAL_CIRCUIT_REGISTRY.get_or_create(channel_name)
    policy = retry_policy or RetryPolicy(max_retries=2, initial_interval=0.05, max_interval=0.5)

    if not breaker.can_execute():
        # Fast fail or invoke fallback
        if fallback_fn:
            try:
                fallback_res = fallback_fn()
                return {
                    "success": True,
                    "channel": channel_name,
                    "circuit_state": breaker.state,
                    "fallback_invoked": True,
                    "result": fallback_res,
                }
            except Exception as e:
                return {
                    "success": False,
                    "channel": channel_name,
                    "circuit_state": breaker.state,
                    "error": f"Circuit OPEN and fallback failed: {str(e)}",
                }

        return {
            "success": False,
            "channel": channel_name,
            "circuit_state": breaker.state,
            "error": f"Circuit breaker for channel '{channel_name}' is OPEN (fast-fail)",
        }

    try:
        result = policy.execute(dispatch_fn)
        breaker.record_success()
        return {
            "success": True,
            "channel": channel_name,
            "circuit_state": breaker.state,
            "result": result,
        }
    except Exception as ex:
        breaker.record_failure(str(ex))
        if fallback_fn:
            try:
                fallback_res = fallback_fn()
                return {
                    "success": True,
                    "channel": channel_name,
                    "circuit_state": breaker.state,
                    "fallback_invoked": True,
                    "result": fallback_res,
                }
            except Exception as fb_ex:
                return {
                    "success": False,
                    "channel": channel_name,
                    "circuit_state": breaker.state,
                    "error": f"Primary failed ({str(ex)}) and fallback failed ({str(fb_ex)})",
                }

        return {
            "success": False,
            "channel": channel_name,
            "circuit_state": breaker.state,
            "error": str(ex),
        }
