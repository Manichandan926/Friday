"""
circuit_breaker.py — Circuit Breaker Pattern for FRIDAY.

Prevents the system from hanging by repeatedly calling failing external APIs
(Gmail, LLMs, MQTT).

States:
    CLOSED: Traffic flows normally.
    OPEN: Traffic is rejected immediately (fail-fast) after failure threshold.
    HALF_OPEN: Test traffic is allowed to see if the service recovered.
"""

from __future__ import annotations

import time
import threading
from enum import Enum
from typing import Callable, Any, Optional

from app.core.logger import logger


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """Raised when the circuit breaker is OPEN."""
    pass


class CircuitBreaker:
    """Protects external service calls from cascading failures.
    
    Usage::
    
        breaker = CircuitBreaker("openai_api", failure_threshold=5, recovery_timeout=30)
        try:
            res = breaker.execute(my_api_call, arg1, arg2)
        except CircuitBreakerOpenException:
            # Fallback logic
    """

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def execute(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute a function through the circuit breaker."""
        with self._lock:
            if self.state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    logger.info(f"CircuitBreaker '{self.name}': Transitioned to HALF_OPEN")
                else:
                    raise CircuitBreakerOpenException(f"Circuit '{self.name}' is OPEN")

        try:
            # Execute the call
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            if not isinstance(e, CircuitBreakerOpenException):
                self._on_failure()
            raise e

    def _on_success(self) -> None:
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failures = 0
                logger.info(f"CircuitBreaker '{self.name}': Transitioned to CLOSED (Recovered)")
            elif self.state == CircuitState.CLOSED and self.failures > 0:
                self.failures = 0

    def _on_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.last_failure_time = time.monotonic()
            
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                logger.warning(f"CircuitBreaker '{self.name}': Transitioned to OPEN (Failed in Half-Open)")
            elif self.state == CircuitState.CLOSED and self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(f"CircuitBreaker '{self.name}': Transitioned to OPEN (Threshold reached)")
