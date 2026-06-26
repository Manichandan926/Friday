"""
plugin_manager.py — Dynamic plugin loader and lifecycle manager for FRIDAY.

Discovers, loads, validates, and manages the lifecycle of FridayPlugin
instances. Plugins are loaded from the `app/plugins/` directory using
importlib dynamic imports.

Architecture:
    PluginManager
        ├── discover()    → scan app/plugins/ for plugin modules
        ├── load()        → import and instantiate FridayPlugin subclasses
        ├── activate()    → call plugin.initialize() + plugin.activate()
        └── deactivate()  → call plugin.deactivate() on shutdown
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

from app.core.events.event import Event, Priority
from app.core.events.event_bus import EventBus
from app.core.events.event_types import EventType
from app.core.logger import logger
from app.core.plugin import FridayPlugin


# Default plugin search directory
_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


class PluginManager:
    """Manages the full lifecycle of FRIDAY plugins.

    Usage::

        pm = PluginManager(bus=EventBus.get_instance())
        pm.discover()      # scan for plugins
        pm.load_all()      # instantiate discovered plugins
        pm.activate_all()  # start all plugins
        pm.deactivate_all() # shutdown
    """

    def __init__(self, bus: EventBus, registry: Optional[object] = None):
        self._bus = bus
        self._registry = registry
        self._discovered: Dict[str, Path] = {}         # name -> module path
        self._plugins: Dict[str, FridayPlugin] = {}    # name -> instance
        self._active: Dict[str, bool] = {}             # name -> is_active

    # ── Discovery ─────────────────────────────────────────

    def discover(self, plugins_dir: Optional[Path] = None) -> List[str]:
        """Scan the plugins directory for valid plugin packages.

        A valid plugin package must contain an `__init__.py` that
        exposes a `Plugin` class (or any FridayPlugin subclass).

        Returns list of discovered plugin names.
        """
        search_dir = plugins_dir or _PLUGINS_DIR
        if not search_dir.exists():
            logger.warning(f"PluginManager: plugins directory not found: {search_dir}")
            return []

        discovered = []
        for item in search_dir.iterdir():
            if item.is_dir() and (item / "__init__.py").exists():
                self._discovered[item.name] = item
                discovered.append(item.name)
                logger.debug(f"PluginManager: discovered plugin → {item.name}")

        logger.info(f"PluginManager: discovered {len(discovered)} plugin(s)")
        return discovered

    # ── Loading ───────────────────────────────────────────

    def load(self, plugin_name: str) -> Optional[FridayPlugin]:
        """Load a single plugin by name.

        Imports the plugin module, finds the FridayPlugin subclass,
        instantiates it, and stores it in the plugin registry.
        """
        if plugin_name in self._plugins:
            logger.warning(f"PluginManager: plugin '{plugin_name}' already loaded")
            return self._plugins[plugin_name]

        plugin_path = self._discovered.get(plugin_name)
        if not plugin_path:
            logger.error(f"PluginManager: plugin '{plugin_name}' not discovered")
            return None

        try:
            # Build the full module path
            module_name = f"app.plugins.{plugin_name}"

            # Import the module
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                spec = importlib.util.spec_from_file_location(
                    module_name, plugin_path / "__init__.py",
                    submodule_search_locations=[str(plugin_path)]
                )
                if spec is None or spec.loader is None:
                    logger.error(f"PluginManager: cannot find module spec for {plugin_name}")
                    return None

                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

            # Find the FridayPlugin subclass
            plugin_cls = self._find_plugin_class(module)
            if plugin_cls is None:
                logger.error(
                    f"PluginManager: no FridayPlugin subclass found in {plugin_name}"
                )
                return None

            # Instantiate
            instance = plugin_cls()
            self._plugins[plugin_name] = instance
            self._active[plugin_name] = False
            logger.info(f"PluginManager: loaded plugin '{instance.name}' v{instance.version}")
            return instance

        except Exception as e:
            logger.error(f"PluginManager: failed to load '{plugin_name}': {e}")
            return None

    def load_all(self) -> int:
        """Load all discovered plugins. Returns count of successfully loaded."""
        loaded = 0
        for name in list(self._discovered.keys()):
            if self.load(name) is not None:
                loaded += 1
        return loaded

    # ── Activation ────────────────────────────────────────

    def activate(self, plugin_name: str) -> bool:
        """Initialize and activate a loaded plugin."""
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            logger.error(f"PluginManager: cannot activate '{plugin_name}' — not loaded")
            return False

        if self._active.get(plugin_name, False):
            logger.warning(f"PluginManager: plugin '{plugin_name}' already active")
            return True

        try:
            # Initialize with bus and registry references
            plugin.initialize(self._bus, self._registry)

            # Activate (subscribe to events, register tools, etc)
            plugin.activate()
            self._active[plugin_name] = True

            # Emit plugin loaded event
            self._bus.publish_sync(Event(
                event_type=EventType.PLUGIN_LOADED,
                source="plugin_manager",
                payload={"plugin": plugin.name, "version": plugin.version},
                priority=Priority.LOW,
            ))

            logger.info(f"PluginManager: activated plugin '{plugin.name}'")
            return True

        except Exception as e:
            logger.error(f"PluginManager: failed to activate '{plugin_name}': {e}")
            # Emit error event
            self._bus.publish_sync(Event(
                event_type=EventType.PLUGIN_ERROR,
                source="plugin_manager",
                payload={"plugin": plugin_name, "error": str(e)},
                priority=Priority.HIGH,
            ))
            return False

    def activate_all(self) -> int:
        """Activate all loaded plugins. Returns count of successfully activated."""
        activated = 0
        for name in list(self._plugins.keys()):
            if self.activate(name):
                activated += 1
        return activated

    # ── Deactivation ──────────────────────────────────────

    def deactivate(self, plugin_name: str) -> bool:
        """Deactivate a running plugin."""
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            return False

        try:
            plugin.deactivate()
            self._active[plugin_name] = False

            self._bus.publish_sync(Event(
                event_type=EventType.PLUGIN_UNLOADED,
                source="plugin_manager",
                payload={"plugin": plugin.name},
                priority=Priority.LOW,
            ))

            logger.info(f"PluginManager: deactivated plugin '{plugin.name}'")
            return True
        except Exception as e:
            logger.error(f"PluginManager: error deactivating '{plugin_name}': {e}")
            return False

    def deactivate_all(self) -> None:
        """Deactivate all active plugins (called on shutdown)."""
        for name in list(self._plugins.keys()):
            if self._active.get(name, False):
                self.deactivate(name)

    # ── Introspection ─────────────────────────────────────

    def get_plugin(self, name: str) -> Optional[FridayPlugin]:
        """Get a loaded plugin instance by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, str]]:
        """List all loaded plugins with their status."""
        result = []
        for name, plugin in self._plugins.items():
            result.append({
                "name": plugin.name,
                "version": plugin.version,
                "description": plugin.description,
                "active": self._active.get(name, False),
                "capabilities": [c.value for c in plugin.capabilities],
            })
        return result

    # ── Internal Helpers ──────────────────────────────────

    @staticmethod
    def _find_plugin_class(module) -> Optional[Type[FridayPlugin]]:
        """Find the first FridayPlugin subclass in a module."""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, FridayPlugin)
                and attr is not FridayPlugin
            ):
                return attr
        return None
