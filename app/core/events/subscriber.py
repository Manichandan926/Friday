"""
subscriber.py — Subscriber definitions for the FRIDAY Event Bus.

Provides both a class-based Subscriber interface and a lightweight
@subscriber decorator for quick inline subscriptions.
"""

from __future__ import annotations

import asyncio
import functools
from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, List, Optional, Union

from app.core.events.event import Event


class Subscriber(ABC):
    """Base class for event subscribers.

    Subclass and implement `handle()` to create a reusable subscriber
    that can be registered with the EventBus.
    """

    @property
    @abstractmethod
    def subscribed_events(self) -> List[str]:
        """Return a list of event_type strings this subscriber wants."""
        ...

    @abstractmethod
    async def handle(self, event: Event) -> None:
        """Process an incoming event. Must be async."""
        ...

    @property
    def name(self) -> str:
        """Human-readable subscriber name for logs."""
        return self.__class__.__name__


class FunctionSubscriber:
    """Wraps a plain function (sync or async) as a subscriber.

    Created automatically by the @subscriber decorator or by
    EventBus.subscribe() when a callable is passed directly.
    """

    def __init__(
        self,
        func: Union[Callable, Coroutine],
        event_types: List[str],
        name: Optional[str] = None,
    ):
        self._func = func
        self._event_types = event_types
        self._name = name or func.__name__
        self._is_async = asyncio.iscoroutinefunction(func)

    @property
    def subscribed_events(self) -> List[str]:
        return self._event_types

    @property
    def name(self) -> str:
        return self._name

    async def handle(self, event: Event) -> None:
        """Invoke the wrapped function. Handles both sync and async."""
        if self._is_async:
            await self._func(event)
        else:
            self._func(event)


def subscriber(*event_types: str) -> Callable:
    """Decorator to register a function as an event subscriber.

    Usage::

        @subscriber(EventType.TASK_CREATED, EventType.TASK_COMPLETED)
        async def on_task_change(event: Event):
            print(f"Task event: {event.event_type}")

    The decorated function gains a `.subscriber` attribute containing
    the FunctionSubscriber instance that can be registered with the bus.
    """
    def decorator(func: Callable) -> Callable:
        types = [str(t) for t in event_types]
        func_sub = FunctionSubscriber(func, types)

        @functools.wraps(func)
        async def wrapper(event: Event) -> None:
            await func_sub.handle(event)

        wrapper.subscriber = func_sub  # type: ignore[attr-defined]
        return wrapper

    return decorator
