"""Circuit breaker for Agent LLM calls.

States:
  CLOSED   → normal operation, every call goes through
  OPEN     → failing fast, returns mock response immediately
  HALF_OPEN → after cooldown, allows one probe call

Transitions:
  CLOSED → OPEN:   failure_rate > 50% over last 5 calls
  OPEN   → HALF_OPEN: after 60s cooldown
  HALF_OPEN → CLOSED: probe succeeds
  HALF_OPEN → OPEN:   probe fails
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from roundtable.models import CircuitState
from roundtable import db

logger = logging.getLogger("roundtable.sentinel.circuit")

_FAILURE_THRESHOLD = 0.5   # 50% failure rate
_MIN_CALLS = 5             # minimum calls before evaluation
_COOLDOWN_SECONDS = 60     # cooldown before half_open


class CircuitBreaker:
    """Per-agent circuit breaker with persistent state."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._open_time: float | None = None
        self._load_state()

    def _load_state(self) -> None:
        row = db.get_agent_health(self.agent_id)
        if row:
            self._state = CircuitState(row.get("circuit_state", "closed"))
            self._failure_count = row.get("failure_count", 0)
            self._success_count = row.get("success_count", 0)

    @property
    def state(self) -> CircuitState:
        # Auto-transition OPEN → HALF_OPEN after cooldown
        if self._state == CircuitState.OPEN:
            if self._open_time and (time.time() - self._open_time) > _COOLDOWN_SECONDS:
                self._state = CircuitState.HALF_OPEN
                self._persist()
                logger.info("[%s] Circuit breaker: OPEN → HALF_OPEN", self.agent_id)
        return self._state

    def can_execute(self) -> bool:
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        self._success_count += 1
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info("[%s] Circuit breaker: HALF_OPEN → CLOSED", self.agent_id)
        self._persist()

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()

        total = self._failure_count + self._success_count
        if total >= _MIN_CALLS:
            failure_rate = self._failure_count / total
            if failure_rate > _FAILURE_THRESHOLD and self._state != CircuitState.OPEN:
                self._state = CircuitState.OPEN
                self._open_time = time.time()
                logger.warning(
                    "[%s] Circuit breaker: CLOSED → OPEN (failure_rate=%.1f%%)",
                    self.agent_id, failure_rate * 100,
                )
                # Insert sentinel alert (skip if no session context)
                pass

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._open_time = time.time()
            logger.warning("[%s] Circuit breaker: HALF_OPEN → OPEN", self.agent_id)

        self._persist()

    def _persist(self) -> None:
        db.upsert_agent_health(
            agent_id=self.agent_id,
            status="healthy" if self._state == CircuitState.CLOSED else "degraded",
            circuit_state=self._state.value,
        )

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._open_time = None
        db.reset_agent_health(self.agent_id)
        logger.info("[%s] Circuit breaker: manually reset to CLOSED", self.agent_id)


# ── Singleton cache ──

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(agent_id: str) -> CircuitBreaker:
    """Get or create a circuit breaker for an agent."""
    if agent_id not in _breakers:
        _breakers[agent_id] = CircuitBreaker(agent_id)
    return _breakers[agent_id]
