import pytest
import time
from app.core.rate_limiter import RateLimiter, TokenBucket

def test_create_bucket():
    limiter = RateLimiter.get_instance()
    limiter.create_bucket("bucket_create_test", capacity=10, refill_per_minute=30)
    
    bucket = limiter._buckets.get("bucket_create_test")
    assert bucket is not None
    assert bucket.capacity == 10
    assert bucket.tokens == 10.0
    # Refill rate per second: 30 / 60 = 0.5
    assert bucket.refill_rate == 0.5

def test_acquire_within_limit():
    limiter = RateLimiter.get_instance()
    limiter.create_bucket("bucket_acquire_test", capacity=5, refill_per_minute=60)
    
    # Can acquire up to 5 tokens
    for _ in range(5):
        assert limiter.acquire("bucket_acquire_test", tokens_needed=1) is True
        
    # Bucket should be empty now
    bucket = limiter._buckets.get("bucket_acquire_test")
    assert bucket.tokens < 1.0

def test_acquire_exceeds_limit():
    limiter = RateLimiter.get_instance()
    limiter.create_bucket("bucket_limit_test", capacity=3, refill_per_minute=60)
    
    assert limiter.acquire("bucket_limit_test", tokens_needed=2) is True
    # Only 1 token left, trying to acquire 2 should fail
    assert limiter.acquire("bucket_limit_test", tokens_needed=2) is False

def test_token_refill():
    limiter = RateLimiter.get_instance()
    # 600 tokens/min = 10 tokens/sec
    limiter.create_bucket("bucket_refill_test", capacity=5, refill_per_minute=600)
    
    # Acquire all tokens
    assert limiter.acquire("bucket_refill_test", tokens_needed=5) is True
    assert limiter.acquire("bucket_refill_test", tokens_needed=1) is False
    
    # Wait 0.3 seconds. Refill rate is 10 tokens/sec, so 3 tokens should be refilled
    time.sleep(0.35)
    
    # Try acquiring 2 tokens, which should succeed
    assert limiter.acquire("bucket_refill_test", tokens_needed=2) is True

def test_missing_bucket_allows():
    limiter = RateLimiter.get_instance()
    # Acquiring from missing bucket should default to True
    assert limiter.acquire("non_existent_bucket") is True
