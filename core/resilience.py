"""Bionics Error Resilience Layer — Circuit breaker, retry logic, error classification."""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("bionics.resilience")


@dataclass
class RetryConfig:
    """Configuration for API retry behavior."""
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True


class CircuitBreaker:
    """Prevents hammering a failing API.

    States:
        CLOSED  — normal operation, requests pass through
        OPEN    — API is down, reject immediately
        HALF_OPEN — recovery window, allow one test request
    """
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.state = self.CLOSED
        self.failures = 0
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = 0.0

    def can_proceed(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = self.HALF_OPEN
                logger.info("Circuit breaker → HALF_OPEN (recovery window)")
                return True
            return False
        return True  # HALF_OPEN: allow one test call

    def record_success(self):
        if self.state == self.HALF_OPEN:
            logger.info("Circuit breaker → CLOSED (recovery successful)")
        self.failures = 0
        self.state = self.CLOSED

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold:
            self.state = self.OPEN
            logger.warning(
                f"Circuit breaker → OPEN after {self.failures} failures "
                f"(recovery in {self.recovery_timeout}s)"
            )

    def reset(self):
        self.state = self.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0
