import pytest
import json
from app.core.policy_engine import PolicyEngine, PolicyRequest, Decision
from app.memory.database import get_db_session
from app.memory.models import PolicyRule

def test_allow_rule():
    engine = PolicyEngine()
    engine.add_rule("unlock_door", role="owner", allowed=True)
    
    req = PolicyRequest(action="unlock_door", role="owner")
    decision = engine.evaluate(req)
    assert decision.decision == Decision.ALLOW
    assert decision.conditions_met is True

def test_deny_rule():
    engine = PolicyEngine()
    engine.add_rule("system_reboot", role="guest", allowed=False)
    
    req = PolicyRequest(action="system_reboot", role="guest")
    decision = engine.evaluate(req)
    assert decision.decision == Decision.DENY
    assert "explicitly denied" in decision.reason

def test_mfa_requirement():
    engine = PolicyEngine()
    engine.add_rule("delete_database", role="owner", allowed=True, requires_mfa=True)
    
    req = PolicyRequest(action="delete_database", role="owner")
    decision = engine.evaluate(req)
    assert decision.decision == Decision.REQUIRE_MFA
    assert "requires MFA" in decision.reason

def test_least_privilege_default_deny():
    engine = PolicyEngine()
    
    req = PolicyRequest(action="secret_action", role="owner")
    decision = engine.evaluate(req)
    assert decision.decision == Decision.DENY
    assert "No policy rule found" in decision.reason

def test_policy_conditions():
    engine = PolicyEngine()
    
    # 1. Simple key-value match condition
    engine.add_rule(
        "access_room", 
        role="guest", 
        allowed=True, 
        conditions={"room_id": "101"}
    )
    
    # Matching context
    req_match = PolicyRequest(action="access_room", role="guest", context={"room_id": "101"})
    assert engine.evaluate(req_match).decision == Decision.ALLOW
    
    # Mismatching context
    req_mismatch = PolicyRequest(action="access_room", role="guest", context={"room_id": "102"})
    assert engine.evaluate(req_mismatch).decision == Decision.DENY
    
    # 2. Registered custom condition evaluator
    engine.register_condition("business_hours", lambda ctx: 9 <= ctx.get("hour", 0) <= 17)
    engine.add_rule(
        "run_task",
        role="employee",
        allowed=True,
        conditions={"business_hours": True}
    )
    
    # Inside business hours
    req_in = PolicyRequest(action="run_task", role="employee", context={"hour": 10})
    assert engine.evaluate(req_in).decision == Decision.ALLOW
    
    # Outside business hours
    req_out = PolicyRequest(action="run_task", role="employee", context={"hour": 20})
    assert engine.evaluate(req_out).decision == Decision.DENY

def test_db_policy_rules():
    # Insert rules into the DB
    with get_db_session() as session:
        r1 = PolicyRule(
            action="open_safe",
            role="manager",
            allowed=True,
            requires_mfa=False
        )
        r2 = PolicyRule(
            action="open_safe",
            role="clerk",
            allowed=False,
            requires_mfa=False
        )
        session.add(r1)
        session.add(r2)
        
    engine = PolicyEngine()
    loaded = engine.load_rules_from_db()
    assert loaded == 2
    
    # Test DB manager rule
    req_mgr = PolicyRequest(action="open_safe", role="manager")
    assert engine.evaluate(req_mgr).decision == Decision.ALLOW
    
    # Test DB clerk rule
    req_clerk = PolicyRequest(action="open_safe", role="clerk")
    assert engine.evaluate(req_clerk).decision == Decision.DENY
