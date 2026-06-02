"""Sentinel — system stability, hallucination detection, and circuit breaking.

Usage:
    from roundtable.sentinel import CircuitBreaker, HallucinationDetector, SentinelMonitor
    breaker = CircuitBreaker(agent_id="product_manager")
    detector = HallucinationDetector()
    monitor = SentinelMonitor()
"""

from roundtable.sentinel.circuit_breaker import CircuitBreaker, get_circuit_breaker
from roundtable.sentinel.hallucination import HallucinationDetector

__all__ = [
    "CircuitBreaker",
    "get_circuit_breaker",
    "HallucinationDetector",
]
