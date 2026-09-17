"""
Unit tests for Fault-Tolerance Mesh: Circuit Breaker, Exponential Jitter Retry, and Anti-Replay Guard.
"""

import time
import pytest
from sovereign_agent_bridge.fault_tolerance import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerRegistry,
    RetryPolicy,
    AntiReplayGuard,
    execute_resilient_channel_dispatch,
)
from sovereign_agent_bridge.mcp_server import MCPServer


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="telegram", failure_threshold=2, recovery_timeout=0.1)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_circuit_trips_to_open_after_threshold(self):
        cb = CircuitBreaker(name="signal", failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure("Socket timeout")
        assert cb.state == CircuitState.CLOSED

        cb.record_failure("Connection refused")
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_circuit_half_open_and_recovery(self):
        cb = CircuitBreaker(name="matrix", failure_threshold=2, recovery_timeout=0.02, half_open_success_threshold=2)
        cb.record_failure("Timeout 1")
        cb.record_failure("Timeout 2")
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout to transition to HALF_OPEN
        time.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True

        # First trial success
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN

        # Second trial success -> restores CLOSED state
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_manual_reset(self):
        cb = CircuitBreaker(name="webhook", failure_threshold=1)
        cb.record_failure("HTTP 500")
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True


class TestRetryPolicy:
    def test_retry_calculation(self):
        policy = RetryPolicy(max_retries=3, initial_interval=0.1, max_interval=1.0, multiplier=2.0, jitter=False)
        assert policy.calculate_delay(0) == 0.1
        assert policy.calculate_delay(1) == 0.2
        assert policy.calculate_delay(2) == 0.4
        assert policy.calculate_delay(3) == 0.8

    def test_retry_success_after_failure(self):
        attempts = 0

        def flaky_func():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("Transient glitch")
            return "SUCCESS"

        policy = RetryPolicy(max_retries=3, initial_interval=0.01)
        result = policy.execute(flaky_func)
        assert result == "SUCCESS"
        assert attempts == 3

    def test_retry_terminal_fallback(self):
        def failing_func():
            raise TimeoutError("Dead network")

        policy = RetryPolicy(max_retries=2, initial_interval=0.01)
        result = policy.execute(failing_func, fallback=lambda exc: f"FALLBACK: {type(exc).__name__}")
        assert "FALLBACK: TimeoutError" in result


class TestAntiReplayGuard:
    def test_valid_first_nonce(self):
        guard = AntiReplayGuard(window_seconds=60.0)
        valid, err = guard.verify_and_record("nonce-12345", timestamp=time.time())
        assert valid is True
        assert err is None

    def test_replay_duplicate_nonce_rejected(self):
        guard = AntiReplayGuard(window_seconds=60.0)
        ts = time.time()
        valid, _ = guard.verify_and_record("nonce-unique-abc", timestamp=ts)
        assert valid is True

        # Attempt to replay the exact same nonce
        replayed, err = guard.verify_and_record("nonce-unique-abc", timestamp=ts)
        assert replayed is False
        assert "Replay attack" in err

    def test_expired_timestamp_rejected(self):
        guard = AntiReplayGuard(window_seconds=10.0)
        old_ts = time.time() - 100.0  # 100 seconds ago
        valid, err = guard.verify_and_record("nonce-old", timestamp=old_ts)
        assert valid is False
        assert "expired" in err.lower()

    def test_hmac_signature_validation(self):
        import hmac
        import hashlib

        guard = AntiReplayGuard(window_seconds=60.0)
        nonce = "nonce-sig-99"
        ts = time.time()
        payload = b'{"action":"grant_lease"}'
        secret = "super-secret-swarm-key"

        expected_sig = hmac.new(
            secret.encode("utf-8"),
            f"{nonce}:{ts}:".encode("utf-8") + payload,
            hashlib.sha256,
        ).hexdigest()

        # Valid signature
        valid, err = guard.verify_and_record(
            nonce=nonce, timestamp=ts, signature=expected_sig, payload_bytes=payload, secret=secret
        )
        assert valid is True
        assert err is None

        # Tampered signature
        tampered_nonce = "nonce-sig-100"
        bad_sig = "deadbeef" * 8
        valid_bad, err_bad = guard.verify_and_record(
            nonce=tampered_nonce, timestamp=ts, signature=bad_sig, payload_bytes=payload, secret=secret
        )
        assert valid_bad is False
        assert "mismatch" in err_bad.lower()


class TestResilientDispatchAndMCP:
    def test_resilient_channel_dispatch_success(self):
        res = execute_resilient_channel_dispatch(
            channel_name="test_chan",
            dispatch_fn=lambda: "MSG_DELIVERED",
        )
        assert res["success"] is True
        assert res["result"] == "MSG_DELIVERED"
        assert res["circuit_state"] == CircuitState.CLOSED

    def test_resilient_channel_dispatch_fallback(self):
        res = execute_resilient_channel_dispatch(
            channel_name="failing_chan",
            dispatch_fn=lambda: (_ for _ in ()).throw(RuntimeError("Unreachable")),
            fallback_fn=lambda: "BACKUP_SMS_SENT",
            retry_policy=RetryPolicy(max_retries=1, initial_interval=0.01),
        )
        assert res["success"] is True
        assert res["fallback_invoked"] is True
        assert res["result"] == "BACKUP_SMS_SENT"

    def test_mcp_bridge_fault_tolerance_metrics(self):
        srv = MCPServer()
        res = srv.execute_tool("bridge_fault_tolerance_metrics", {})
        assert res["isError"] is False
        import json
        data = json.loads(res["content"][0]["text"])
        assert "circuits" in data
        assert "anti_replay" in data
