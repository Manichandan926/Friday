"""
plugin.py — Plugin SDK base classes for FRIDAY.

Every FRIDAY plugin must subclass FridayPlugin and implement
the lifecycle methods. Plugins declare their capabilities,
subscribe to events, and register tools through a clean interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import unique, Enum
from typing import Any, Callable, Dict, List, Optional


@unique
class Capability(str, Enum):
    """Capabilities a plugin or agent can declare.

    Used by the Permission System to control what resources
    a plugin is allowed to access.
    """
    EMAIL           = "email"
    TASKS           = "tasks"
    PROJECTS        = "projects"
    KNOWLEDGE       = "knowledge"
    MEMORY          = "memory"
    SHELL           = "shell"
    NETWORK         = "network"
    HOME_CONTROL    = "home_control"
    SECURITY        = "security"
    CAMERA          = "camera"
    MICROPHONE      = "microphone"
    DEVICE_CONTROL  = "device_control"
    NOTIFICATIONS   = "notifications"
    SYSTEM_INFO     = "system_info"
    FILE_READ       = "file_read"
    FILE_WRITE      = "file_write"


class FridayPlugin(ABC):
    """Base class for all FRIDAY plugins.

    Lifecycle::

        plugin = MyPlugin()
        plugin.initialize(bus, registry)   # called by PluginManager
        plugin.activate()                  # start subscriptions / jobs
        plugin.deactivate()                # cleanup on shutdown
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g. 'internship_scanner')."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version string (e.g. '1.0.0')."""
        ...

    @property
    def description(self) -> str:
        """Short human-readable description."""
        return ""

    @property
    def capabilities(self) -> List[Capability]:
        """Capabilities this plugin requires (checked by PermissionSystem)."""
        return []

    def initialize(self, bus: Any, registry: Any) -> None:
        """Called once by PluginManager with the EventBus and AgentRegistry.

        Store references for use in activate() and event handlers.
        """
        self._bus = bus
        self._registry = registry

    @abstractmethod
    def activate(self) -> None:
        """Subscribe to events, register tools, start background work."""
        ...

    def deactivate(self) -> None:
        """Cleanup: unsubscribe, stop jobs. Called on shutdown."""
        pass

    def get_tools(self) -> Dict[str, Callable]:
        """Return tool functions to register in the Tool Registry.

        Override to expose plugin-specific tools::

            def get_tools(self):
                return {"scan_internships": self.scan}
        """
        return {}

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        """Return APScheduler job definitions.

        Override to schedule background work::

            def get_scheduled_jobs(self):
                return [{"func": self.poll, "trigger": "interval", "minutes": 10}]
        """
        return []
