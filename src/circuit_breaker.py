"""Per-tenant circuit breaker for external service calls.

States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing).
Trips after `failure_threshold` failures within `window_sec`.
Resets after `reset_timeout_sec` of no failures.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from enum import Enum
from threading import Lock

logger = logging.getLogger("voicebuddy.circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for a single tenant / service pair."""

    def __init__(
        self,
        failure_threshold: int = 3,
        window_sec: float = 60.0,
        reset_timeout_sec: float = 120.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.window_sec = window_sec
        self.reset_timeout_sec = reset_timeout_sec

        self._state = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at: float = 0.0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.reset_timeout_sec:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def record_failure(self) -> None:
        """Record a failure. Trips the breaker if threshold is reached."""
        now = time.monotonic()
        with self._lock:
            self._failures.append(now)
            self._prune(now)
            if len(self._failures) >= self.failure_threshold:
                if self._state != CircuitState.OPEN:
                    logger.warning("Circuit breaker OPEN (failures=%d)", len(self._failures))
                self._state = CircuitState.OPEN
                self._opened_at = now

    def record_success(self) -> None:
        """Record a success. Resets from HALF_OPEN → CLOSED."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit breaker CLOSED (probe succeeded)")
                self._state = CircuitState.CLOSED
                self._failures.clear()

    def is_call_allowed(self) -> bool:
        """Return True if the circuit allows a call through."""
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def reset(self) -> None:
        """Force-reset to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures.clear()
            self._opened_at = 0.0

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of current state."""
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            return {
                "state": self._state.value,
                "recent_failures": len(self._failures),
                "opened_at": self._opened_at if self._opened_at else None,
            }

    def _prune(self, now: float) -> None:
        """Remove failures outside the sliding window."""
        cutoff = now - self.window_sec
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()


class CircuitBreakerRegistry:
    """Global registry of per-tenant circuit breakers."""

    def __init__(
        self,
        failure_threshold: int = 3,
        window_sec: float = 60.0,
        reset_timeout_sec: float = 120.0,
    ) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = Lock()
        self._failure_threshold = failure_threshold
        self._window_sec = window_sec
        self._reset_timeout_sec = reset_timeout_sec

    def get(self, tenant_id: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a tenant."""
        with self._lock:
            if tenant_id not in self._breakers:
                self._breakers[tenant_id] = CircuitBreaker(
                    failure_threshold=self._failure_threshold,
                    window_sec=self._window_sec,
                    reset_timeout_sec=self._reset_timeout_sec,
                )
            return self._breakers[tenant_id]

    def all_snapshots(self) -> dict[str, dict]:
        """Return snapshots for all registered tenants."""
        with self._lock:
            return {tid: cb.snapshot() for tid, cb in self._breakers.items()}
