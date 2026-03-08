"""Tests for circuit_breaker module."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_opens_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_blocks_calls(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_call_allowed()

    def test_closed_allows_calls(self):
        cb = CircuitBreaker()
        assert cb.is_call_allowed()

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout_sec=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.is_call_allowed()

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout_sec=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout_sec=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_failures_outside_window_pruned(self):
        cb = CircuitBreaker(failure_threshold=3, window_sec=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        # Old failures should be pruned, so a third failure alone won't trip
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_snapshot(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        snap = cb.snapshot()
        assert snap["state"] == "closed"
        assert snap["recent_failures"] == 1


class TestCircuitBreakerRegistry:
    def test_get_creates_breaker(self):
        reg = CircuitBreakerRegistry()
        cb = reg.get("tenant-a")
        assert isinstance(cb, CircuitBreaker)
        assert cb is reg.get("tenant-a")

    def test_different_tenants_isolated(self):
        reg = CircuitBreakerRegistry(failure_threshold=1)
        reg.get("tenant-a").record_failure()
        assert reg.get("tenant-a").state == CircuitState.OPEN
        assert reg.get("tenant-b").state == CircuitState.CLOSED

    def test_all_snapshots(self):
        reg = CircuitBreakerRegistry()
        reg.get("t1").record_failure()
        reg.get("t2")
        snaps = reg.all_snapshots()
        assert "t1" in snaps
        assert "t2" in snaps
        assert snaps["t1"]["recent_failures"] == 1
        assert snaps["t2"]["recent_failures"] == 0
