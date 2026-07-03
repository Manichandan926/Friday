"""
command_bus.py — Command Bus for CQRS Separation.

In FRIDAY's architecture:
    Events  = "Something happened" (Broadcast to many subscribers, past tense)
              e.g., DeviceTurnedOn, EmailReceived, MotionDetected
              
    Commands = "Do something" (Routed to exactly ONE handler, imperative)
              e.g., TurnOnDevice, SendEmail, UnlockDoor

Architecture:
    Command → CommandBus → (Policy Engine) → CommandHandler → Execution → Result

This enforces the Command Query Responsibility Segregation (CQRS) pattern.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Type, Union
import uuid

from app.core.logger import logger
from app.core.policy_engine import PolicyEngine, PolicyRequest


@dataclass
class Command:
    """Base Command object. All imperative actions should subclass this."""
    command_name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    role: str = "owner"
    command_id: str = field(default_factory=lambda: str(uuid.uuid7() if hasattr(uuid, 'uuid7') else uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CommandResult:
    """Result of a command execution."""
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Exception] = None


class CommandHandler(ABC):
    """Abstract base class for command handlers."""
    @abstractmethod
    async def handle(self, command: Command) -> CommandResult:
        ...


class FunctionCommandHandler(CommandHandler):
    """Wraps a simple function to act as a CommandHandler."""
    def __init__(self, func: Callable[[Command], Any]):
        self._func = func

    async def handle(self, command: Command) -> CommandResult:
        try:
            if asyncio.iscoroutinefunction(self._func):
                result = await self._func(command)
            else:
                result = self._func(command)
                
            if isinstance(result, CommandResult):
                return result
            return CommandResult(success=True, data={"result": result})
        except Exception as e:
            return CommandResult(success=False, message=str(e), error=e)


class CommandBus:
    """Routes commands to their registered handler.
    
    Validates commands through the Policy Engine before execution.
    Only one handler can be registered per command name.
    
    Usage::
    
        bus = CommandBus(policy_engine)
        bus.register_handler("turn_on_device", device_handler)
        result = await bus.execute(Command(command_name="turn_on_device", ...))
    """

    def __init__(self, policy_engine: Optional[PolicyEngine] = None):
        self._handlers: Dict[str, CommandHandler] = {}
        self._policy = policy_engine

    def register_handler(
        self, 
        command_name: str, 
        handler: Union[CommandHandler, Callable]
    ) -> None:
        """Register a handler for a specific command."""
        if command_name in self._handlers:
            logger.warning(
                f"CommandBus: Overwriting existing handler for '{command_name}'"
            )
            
        if isinstance(handler, CommandHandler):
            self._handlers[command_name] = handler
        else:
            self._handlers[command_name] = FunctionCommandHandler(handler)
            
        logger.debug(f"CommandBus: Registered handler for '{command_name}'")

    def unregister_handler(self, command_name: str) -> None:
        """Remove a command handler."""
        self._handlers.pop(command_name, None)

    async def execute(self, command: Command) -> CommandResult:
        """Execute a command, enforcing policy first."""
        handler = self._handlers.get(command.command_name)
        if not handler:
            msg = f"No handler registered for command '{command.command_name}'"
            logger.error(f"CommandBus: {msg}")
            return CommandResult(success=False, message=msg)

        # Policy Check
        if self._policy:
            decision = self._policy.evaluate(PolicyRequest(
                action=command.command_name,
                role=command.role,
                context=command.payload,
                source=command.source,
            ))
            
            if decision.decision.value != "allow":
                logger.warning(
                    f"CommandBus: Policy DENIED '{command.command_name}' "
                    f"for {command.role}: {decision.reason}"
                )
                return CommandResult(
                    success=False, 
                    message=f"Policy denied: {decision.reason}"
                )

        # Execute
        try:
            logger.debug(
                f"CommandBus: Executing '{command.command_name}' "
                f"[{command.command_id[:8]}]"
            )
            return await handler.handle(command)
        except Exception as e:
            logger.error(
                f"CommandBus: Execution failed for '{command.command_name}': {e}"
            )
            return CommandResult(success=False, message=str(e), error=e)
