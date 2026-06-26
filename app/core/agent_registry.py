"""
agent_registry.py — Centralized Agent Registry for FRIDAY.

Replaces hard-coded if/elif agent selection with a declarative
registration system. Agents register themselves with:
    - Intent keywords they can handle
    - Capabilities they require
    - Priority for conflict resolution

Architecture:
    User query → Intent extraction → AgentRegistry.find(intent) → Agent.handle()

Instead of:
    if query == "email": InboxAgent()
    elif query == "task": TaskAgent()
    ...

Use:
    AgentRegistry.register(InboxAgent)
    agent = AgentRegistry.find("check my email")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Type

from app.core.events.event_bus import EventBus
from app.core.logger import logger
from app.core.plugin import Capability


class BaseAgent(ABC):
    """Abstract base class for all FRIDAY agents.

    Every agent must declare:
        - name: unique identifier
        - description: what this agent does
        - intents: keywords/patterns it can handle
        - capabilities: what resources it needs

    And implement:
        - handle(): process a user request
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique agent name (e.g. 'inbox_agent')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...

    @property
    @abstractmethod
    def intents(self) -> List[str]:
        """Keywords/phrases this agent responds to.

        Example: ["email", "inbox", "mail", "gmail", "unread"]
        """
        ...

    @property
    def capabilities(self) -> List[Capability]:
        """Capabilities this agent requires."""
        return []

    @property
    def priority(self) -> int:
        """Higher priority agents are preferred when multiple match.
        Default 0. Range: 0 (lowest) to 100 (highest).
        """
        return 0

    @abstractmethod
    async def handle(self, query: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Process a user query and return a response string."""
        ...


@dataclass
class AgentEntry:
    """Internal registry entry for a registered agent."""
    agent_class: Type[BaseAgent]
    instance: Optional[BaseAgent] = None
    intents: List[str] = field(default_factory=list)
    capabilities: List[Capability] = field(default_factory=list)
    priority: int = 0
    active: bool = True


class AgentRegistry:
    """Centralized registry for finding and invoking agents by intent.

    Usage::

        registry = AgentRegistry()
        registry.register(InboxAgent)
        registry.register(TaskAgent)

        # Find best matching agent
        agent = registry.find("check my email")
        response = await agent.handle("check my email")
    """

    def __init__(self):
        self._agents: Dict[str, AgentEntry] = {}
        self._intent_index: Dict[str, List[str]] = {}  # intent_keyword → [agent_names]

    def register(self, agent_class: Type[BaseAgent]) -> None:
        """Register an agent class with the registry.

        Creates a temporary instance to read metadata, then stores
        the class for lazy instantiation.
        """
        try:
            temp = agent_class()
            name = temp.name
            intents = temp.intents
            caps = temp.capabilities
            prio = temp.priority

            entry = AgentEntry(
                agent_class=agent_class,
                intents=intents,
                capabilities=caps,
                priority=prio,
            )
            self._agents[name] = entry

            # Build intent index
            for intent in intents:
                intent_lower = intent.lower()
                if intent_lower not in self._intent_index:
                    self._intent_index[intent_lower] = []
                if name not in self._intent_index[intent_lower]:
                    self._intent_index[intent_lower].append(name)

            logger.debug(f"AgentRegistry: registered '{name}' with intents {intents}")

        except Exception as e:
            logger.error(f"AgentRegistry: failed to register {agent_class.__name__}: {e}")

    def unregister(self, agent_name: str) -> bool:
        """Remove an agent from the registry."""
        entry = self._agents.pop(agent_name, None)
        if entry is None:
            return False

        # Clean intent index
        for intent in entry.intents:
            intent_lower = intent.lower()
            if intent_lower in self._intent_index:
                self._intent_index[intent_lower] = [
                    n for n in self._intent_index[intent_lower] if n != agent_name
                ]

        logger.debug(f"AgentRegistry: unregistered '{agent_name}'")
        return True

    def find(self, query: str) -> Optional[BaseAgent]:
        """Find the best-matching agent for a query string.

        Matching strategy:
            1. Tokenize query into lowercase words
            2. Check each word against the intent index
            3. Score each matching agent by number of matched intents
            4. Break ties by agent priority
            5. Return the winning agent instance (lazily created)
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())

        # Score agents by intent matches
        scores: Dict[str, int] = {}
        for word in query_words:
            # Direct word match
            if word in self._intent_index:
                for agent_name in self._intent_index[word]:
                    scores[agent_name] = scores.get(agent_name, 0) + 1

            # Substring match (for multi-word intents like "ip address")
            for intent_key, agent_names in self._intent_index.items():
                if intent_key in query_lower and intent_key not in query_words:
                    for agent_name in agent_names:
                        scores[agent_name] = scores.get(agent_name, 0) + 1

        if not scores:
            return None

        # Sort by score (desc), then by priority (desc)
        best_name = max(
            scores.keys(),
            key=lambda n: (scores[n], self._agents[n].priority),
        )

        return self._get_instance(best_name)

    def find_all(self, query: str) -> List[BaseAgent]:
        """Find all agents matching a query, sorted by relevance."""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scores: Dict[str, int] = {}
        for word in query_words:
            if word in self._intent_index:
                for agent_name in self._intent_index[word]:
                    scores[agent_name] = scores.get(agent_name, 0) + 1

        if not scores:
            return []

        sorted_names = sorted(
            scores.keys(),
            key=lambda n: (scores[n], self._agents[n].priority),
            reverse=True,
        )

        return [self._get_instance(n) for n in sorted_names if self._get_instance(n)]

    def get(self, agent_name: str) -> Optional[BaseAgent]:
        """Get an agent instance by name."""
        return self._get_instance(agent_name)

    def list_agents(self) -> List[Dict[str, Any]]:
        """List all registered agents with metadata."""
        return [
            {
                "name": entry.agent_class.__name__,
                "agent_name": name,
                "intents": entry.intents,
                "capabilities": [c.value for c in entry.capabilities],
                "priority": entry.priority,
                "active": entry.active,
            }
            for name, entry in self._agents.items()
        ]

    def _get_instance(self, agent_name: str) -> Optional[BaseAgent]:
        """Get or create an agent instance (lazy initialization)."""
        entry = self._agents.get(agent_name)
        if entry is None:
            return None

        if entry.instance is None:
            try:
                entry.instance = entry.agent_class()
            except Exception as e:
                logger.error(f"AgentRegistry: failed to instantiate '{agent_name}': {e}")
                return None

        return entry.instance
