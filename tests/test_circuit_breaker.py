import pytest
import time
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException, CircuitState

def test_closed_state_allows_calls():
    breaker = CircuitBreaker("test", failure_threshold=3)
    
    def my_call(x):
        return x * 2
        
    assert breaker.execute(my_call, 5) == 10
    assert breaker.state == CircuitState.CLOSED

def test_failure_threshold_opens_circuit():
    breaker = CircuitBreaker("test", failure_threshold=3)
    
    def failing_call():
        raise ValueError("Failed")
        
    # Trigger failures
    for _ in range(3):
        with pytest.raises(ValueError):
            breaker.execute(failing_call)
            
    assert breaker.state == CircuitState.OPEN
    assert breaker.failures == 3

def test_open_state_raises_exception():
    breaker = CircuitBreaker("test", failure_threshold=2)
    
    def failing_call():
        raise ValueError("Failed")
        
    # Open the circuit
    for _ in range(2):
        with pytest.raises(ValueError):
            breaker.execute(failing_call)
            
    assert breaker.state == CircuitState.OPEN
    
    # Try calling again, should raise CircuitBreakerOpenException without invoking the function
    invoked = False
    def my_call():
        nonlocal invoked
        invoked = True
        return True
        
    with pytest.raises(CircuitBreakerOpenException):
        breaker.execute(my_call)
        
    assert invoked is False

def test_recovery_timeout_to_half_open():
    # Set recovery timeout to 0.1s
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    
    def failing_call():
        raise ValueError("Failed")
        
    for _ in range(2):
        with pytest.raises(ValueError):
            breaker.execute(failing_call)
            
    assert breaker.state == CircuitState.OPEN
    
    # Sleep to exceed recovery timeout
    time.sleep(0.15)
    
    # Next call should transition to HALF_OPEN
    def success_call():
        return "recovered"
        
    res = breaker.execute(success_call)
    assert res == "recovered"
    # Success in HALF_OPEN transitions to CLOSED
    assert breaker.state == CircuitState.CLOSED

def test_half_open_success_closes():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    
    def failing_call():
        raise ValueError("Failed")
        
    for _ in range(2):
        with pytest.raises(ValueError):
            breaker.execute(failing_call)
            
    time.sleep(0.15)
    
    # Call successfully
    breaker.execute(lambda: "success")
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failures == 0

def test_half_open_failure_reopens():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    
    def failing_call():
        raise ValueError("Failed")
        
    for _ in range(2):
        with pytest.raises(ValueError):
            breaker.execute(failing_call)
            
    time.sleep(0.15)
    
    # Call fails in HALF_OPEN
    with pytest.raises(ValueError):
        breaker.execute(failing_call)
        
    # Should immediately reopen the circuit
    assert breaker.state == CircuitState.OPEN
