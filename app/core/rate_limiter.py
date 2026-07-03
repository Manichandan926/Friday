"""
rate_limiter.py — Global Rate Limiter for FRIDAY.

Implements the Token Bucket algorithm to protect the system from
runaway API calls, notification spam, or excessive LLM inference.

Architecture:
    Action → RateLimiter.acquire(bucket_name) → Allow / Reject
"""

import time
import threading
from dataclasses import dataclass
from typing import Dict, Optional
from app.core.logger import logger


@dataclass
class TokenBucket:
    capacity: int
    tokens: float
    refill_rate: float  # tokens per second
    last_refill: float


class RateLimiter:
    """Global rate limiting service using Token Bucket algorithm.
    
    Usage::
    
        limiter = RateLimiter.get_instance()
        limiter.create_bucket("llm_calls", capacity=10, refill_per_minute=20)
        
        if limiter.acquire("llm_calls"):
            # Do LLM call
        else:
            # Rate limited
    """

    _instance: Optional['RateLimiter'] = None
    _lock = threading.Lock()

    def __init__(self):
        self._buckets: Dict[str, TokenBucket] = {}
        self._rl_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'RateLimiter':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def create_bucket(self, name: str, capacity: int, refill_per_minute: float) -> None:
        """Create a new rate limiting bucket."""
        with self._rl_lock:
            self._buckets[name] = TokenBucket(
                capacity=capacity,
                tokens=float(capacity),
                refill_rate=refill_per_minute / 60.0,
                last_refill=time.monotonic()
            )
            logger.debug(f"RateLimiter: bucket '{name}' created ({capacity}, {refill_per_minute}/min)")

    def acquire(self, bucket_name: str, tokens_needed: int = 1) -> bool:
        """Attempt to acquire tokens from a bucket."""
        with self._rl_lock:
            bucket = self._buckets.get(bucket_name)
            if not bucket:
                logger.warning(f"RateLimiter: bucket '{bucket_name}' not found, allowing by default.")
                return True
                
            now = time.monotonic()
            elapsed = now - bucket.last_refill
            
            # Refill tokens
            bucket.tokens = min(
                bucket.capacity,
                bucket.tokens + (elapsed * bucket.refill_rate)
            )
            bucket.last_refill = now
            
            # Check availability
            if bucket.tokens >= tokens_needed:
                bucket.tokens -= tokens_needed
                return True
            else:
                logger.warning(f"RateLimiter: '{bucket_name}' rate limit exceeded!")
                return False
