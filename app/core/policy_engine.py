"""
policy_engine.py — Policy evaluation engine for FRIDAY.

Enforces the security principle:
    Intent → Policy Engine → Permission Check → Execution

Every action that could affect devices, files, security, or external
systems must pass through the Policy Engine before executing.

Architecture:
    Request
       ↓
    Intent (action + role + context)
       ↓
    Policy Engine
       ├── Check DB rules (PolicyRule table)
       ├── Check in-memory rules
       ├── Evaluate conditions
       └── Return PolicyDecision
       ↓
    Execution (only if ALLOWED)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Callable, Dict, List, Optional

from app.core.logger import logger
from app.core.audit_system import AuditSystem, AuditEntry


@unique
class Decision(str, Enum):
    """Result of a policy evaluation."""
    ALLOW      = "allow"
    DENY       = "deny"
    REQUIRE_MFA = "require_mfa"


@dataclass
class PolicyRequest:
    """Incoming request to be evaluated by the Policy Engine."""
    action: str                          # e.g. "unlock_door", "delete_file", "run_shell"
    role: str = "owner"                  # owner, guest, plugin, agent
    context: Dict[str, Any] = field(default_factory=dict)  # extra context (device_id, path, etc)
    source: str = ""                     # who is requesting


@dataclass
class PolicyDecision:
    """Result returned by the Policy Engine."""
    decision: Decision
    action: str
    role: str
    reason: str = ""
    conditions_met: bool = True


class PolicyEngine:
    """Evaluates requests against policy rules.

    Rules are loaded from the database (PolicyRule table) on startup,
    and can be supplemented by in-memory rules registered at runtime
    (useful for plugins that define their own policies).

    Default behavior: if no rule matches, the request is DENIED.
    This follows the principle of least privilege.
    """

    def __init__(self, audit_system: Optional[AuditSystem] = None):
        self._memory_rules: Dict[str, List[Dict[str, Any]]] = {}
        self._db_rules_loaded = False
        self._db_rules: Dict[str, List[Dict[str, Any]]] = {}
        self._condition_evaluators: Dict[str, Callable] = {}
        self._audit_system = audit_system

    def load_rules_from_db(self) -> int:
        """Load policy rules from the PolicyRule database table.

        Returns number of rules loaded.
        """
        try:
            from app.memory.database import get_db_session
            from app.memory.models import PolicyRule

            loaded = 0
            with get_db_session() as session:
                rules = session.query(PolicyRule).all()
                for rule in rules:
                    action = rule.action
                    if action not in self._db_rules:
                        self._db_rules[action] = []

                    conditions = {}
                    if rule.conditions:
                        try:
                            conditions = json.loads(rule.conditions)
                        except (json.JSONDecodeError, TypeError):
                            pass

                    self._db_rules[action].append({
                        "role": rule.role,
                        "allowed": rule.allowed,
                        "requires_mfa": rule.requires_mfa,
                        "conditions": conditions,
                    })
                    loaded += 1

            self._db_rules_loaded = True
            logger.info(f"PolicyEngine: loaded {loaded} rules from database")
            return loaded

        except Exception as e:
            logger.error(f"PolicyEngine: failed to load rules from DB: {e}")
            return 0

    def add_rule(
        self,
        action: str,
        role: str = "owner",
        allowed: bool = True,
        requires_mfa: bool = False,
        conditions: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add an in-memory policy rule.

        Example::
            engine.add_rule("unlock_door", role="owner", allowed=True, requires_mfa=True)
            engine.add_rule("unlock_door", role="guest", allowed=False)
        """
        if action not in self._memory_rules:
            self._memory_rules[action] = []

        self._memory_rules[action].append({
            "role": role,
            "allowed": allowed,
            "requires_mfa": requires_mfa,
            "conditions": conditions or {},
        })
        logger.debug(f"PolicyEngine: added rule {action}/{role} → {'allow' if allowed else 'deny'}")

    def register_condition(self, name: str, evaluator: Callable[[Dict], bool]) -> None:
        """Register a custom condition evaluator.

        Example::
            engine.register_condition("time_window", lambda ctx: 9 <= ctx.get("hour", 0) <= 17)
        """
        self._condition_evaluators[name] = evaluator

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate a request against all matching rules.

        Lookup order:
            1. In-memory rules (highest priority — plugins/runtime)
            2. Database rules
            3. Default: DENY (principle of least privilege)
        """
        action = request.action
        role = request.role

        # Search in-memory rules first
        rule = self._find_matching_rule(self._memory_rules, action, role)

        # Fall back to database rules
        if rule is None:
            if not self._db_rules_loaded:
                self.load_rules_from_db()
            rule = self._find_matching_rule(self._db_rules, action, role)

        # No rule found → DENY (least privilege)
        if rule is None:
            return self._finalize_decision(PolicyDecision(
                decision=Decision.DENY,
                action=action,
                role=role,
                reason=f"No policy rule found for action '{action}' with role '{role}'",
            ), request)

        # Rule found but not allowed
        if not rule["allowed"]:
            return self._finalize_decision(PolicyDecision(
                decision=Decision.DENY,
                action=action,
                role=role,
                reason=f"Action '{action}' is explicitly denied for role '{role}'",
            ), request)

        # Check conditions
        conditions = rule.get("conditions", {})
        if conditions:
            conditions_met = self._evaluate_conditions(conditions, request.context)
            if not conditions_met:
                return self._finalize_decision(PolicyDecision(
                    decision=Decision.DENY,
                    action=action,
                    role=role,
                    reason=f"Conditions not met for action '{action}'",
                    conditions_met=False,
                ), request)

        # Check MFA requirement
        if rule.get("requires_mfa", False):
            return self._finalize_decision(PolicyDecision(
                decision=Decision.REQUIRE_MFA,
                action=action,
                role=role,
                reason=f"Action '{action}' requires MFA confirmation",
            ), request)

        # All checks passed
        return self._finalize_decision(PolicyDecision(
            decision=Decision.ALLOW,
            action=action,
            role=role,
            reason="Policy check passed",
        ), request)

    def _finalize_decision(self, decision: PolicyDecision, request: PolicyRequest) -> PolicyDecision:
        """Log decision and return it."""
        if self._audit_system:
            self._audit_system.log(AuditEntry(
                actor=request.source or request.role,
                action=decision.action,
                resource=request.context.get("resource", "system"),
                decision=decision.decision.value.upper(),
                context_data=request.context
            ))
        return decision

    def is_allowed(self, action: str, role: str = "owner", context: Optional[Dict] = None) -> bool:
        """Convenience method: returns True only if the action is ALLOW."""
        decision = self.evaluate(PolicyRequest(
            action=action, role=role, context=context or {}
        ))
        return decision.decision == Decision.ALLOW

    # ── Internal Helpers ──────────────────────────────────

    @staticmethod
    def _find_matching_rule(
        rules_dict: Dict[str, List[Dict]],
        action: str,
        role: str,
    ) -> Optional[Dict]:
        """Find the first rule matching action + role."""
        action_rules = rules_dict.get(action, [])
        for rule in action_rules:
            if rule["role"] == role:
                return rule
        # Check for wildcard role
        for rule in action_rules:
            if rule["role"] == "*":
                return rule
        return None

    def _evaluate_conditions(self, conditions: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Evaluate condition expressions against the request context."""
        for cond_name, cond_value in conditions.items():
            evaluator = self._condition_evaluators.get(cond_name)
            if evaluator:
                try:
                    if not evaluator(context):
                        return False
                except Exception as e:
                    logger.error(f"PolicyEngine: condition '{cond_name}' failed: {e}")
                    return False
            else:
                # Simple key-value match in context
                if context.get(cond_name) != cond_value:
                    return False
        return True
