"""
plugin_sandbox.py — Plugin Sandboxing and Manifest Validation for FRIDAY.

Provides isolation guarantees for plugins by:
    1. Validating a PluginManifest before loading
    2. Enforcing declared capabilities via the CapabilityGraph
    3. Tracking resource usage (CPU budget, memory budget, API call limits)
    4. Killing plugins that exceed their resource allocation

Architecture:
    Plugin Module → PluginManifest validation → PluginSandbox wrapper → PluginManager

Future: When FRIDAY manages a home, local AI cluster, and security cameras,
rouge or buggy plugins must NOT be able to:
    - Consume all CPU (starving the vision pipeline)
    - Access cameras without declaration
    - Send unlimited network requests
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set

from app.core.capability_graph import CapabilityGraph
from app.core.logger import logger


@dataclass
class ResourceLimits:
    """Resource budget for a sandboxed plugin."""
    cpu_percent: float = 100.0        # max CPU% this plugin may use (0–100)
    memory_mb: float = 512.0          # max resident memory in MB
    api_calls_per_minute: int = 60    # max external API calls per minute
    event_rate_per_second: int = 100  # max events published per second
    max_devices: int = 50             # max devices this plugin can control


@dataclass
class PluginManifest:
    """Declarative manifest for a FRIDAY plugin.

    Every plugin must provide a manifest (either as a class attribute or
    via a manifest.json file) so the sandbox can validate and enforce
    its declared boundaries.

    Example::

        manifest = PluginManifest(
            name="smart_lights",
            version="1.2.0",
            description="Controls Philips Hue lights via MQTT",
            author="Mani Chandan",
            capabilities={"device.control", "notifications.send"},
            dependencies=["paho-mqtt"],
            resource_limits=ResourceLimits(cpu_percent=10, memory_mb=64),
        )
    """
    name: str
    version: str
    description: str = ""
    author: str = ""
    capabilities: Set[str] = field(default_factory=set)
    dependencies: List[str] = field(default_factory=list)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    trusted: bool = False  # trusted plugins skip some sandbox checks


@dataclass
class ResourceUsage:
    """Runtime resource usage counters for a sandboxed plugin."""
    api_calls: int = 0
    events_published: int = 0
    last_api_reset: float = field(default_factory=time.monotonic)
    last_event_reset: float = field(default_factory=time.monotonic)
    violations: int = 0
    is_suspended: bool = False

    def reset_counters_if_needed(self) -> None:
        """Reset rate-limit counters every minute / second."""
        now = time.monotonic()
        if now - self.last_api_reset >= 60.0:
            self.api_calls = 0
            self.last_api_reset = now
        if now - self.last_event_reset >= 1.0:
            self.events_published = 0
            self.last_event_reset = now


class PluginSandbox:
    """Validates manifests and enforces resource limits for plugins.

    Usage::

        sandbox = PluginSandbox(capability_graph)

        # Validate before loading
        errors = sandbox.validate_manifest(manifest)
        if errors:
            print(f"Plugin rejected: {errors}")

        # Track resource usage
        sandbox.register(manifest)
        sandbox.record_api_call("smart_lights")
        sandbox.record_event("smart_lights")

        # Check if plugin is within limits
        if sandbox.is_suspended("smart_lights"):
            print("Plugin exceeded resource limits")
    """

    def __init__(self, capability_graph: Optional[CapabilityGraph] = None):
        self._cap_graph = capability_graph or CapabilityGraph()
        self._manifests: Dict[str, PluginManifest] = {}
        self._usage: Dict[str, ResourceUsage] = {}

    # ── Manifest Validation ───────────────────────────────

    def validate_manifest(self, manifest: PluginManifest) -> List[str]:
        """Validate a plugin manifest. Returns list of error strings.

        Empty list = valid manifest.
        """
        errors: List[str] = []

        if not manifest.name:
            errors.append("Plugin name is required")
        if not manifest.version:
            errors.append("Plugin version is required")

        # Validate capabilities exist in the graph
        all_caps = set(self._cap_graph.list_capabilities())
        for cap in manifest.capabilities:
            if cap not in all_caps:
                errors.append(f"Unknown capability: '{cap}'")

        # Validate resource limits
        limits = manifest.resource_limits
        if limits.cpu_percent <= 0 or limits.cpu_percent > 100:
            errors.append(f"Invalid CPU limit: {limits.cpu_percent}%")
        if limits.memory_mb <= 0:
            errors.append(f"Invalid memory limit: {limits.memory_mb}MB")

        return errors

    # ── Registration ──────────────────────────────────────

    def register(self, manifest: PluginManifest) -> bool:
        """Register a validated plugin manifest for tracking."""
        self._manifests[manifest.name] = manifest
        self._usage[manifest.name] = ResourceUsage()
        logger.debug(
            f"PluginSandbox: registered '{manifest.name}' with caps={manifest.capabilities}"
        )
        return True

    def unregister(self, plugin_name: str) -> None:
        """Remove a plugin from tracking."""
        self._manifests.pop(plugin_name, None)
        self._usage.pop(plugin_name, None)

    # ── Capability Checks ─────────────────────────────────

    def check_capability(self, plugin_name: str, required: str) -> bool:
        """Check if a plugin has a required capability."""
        manifest = self._manifests.get(plugin_name)
        if manifest is None:
            return False
        if manifest.trusted:
            return True
        return self._cap_graph.has_capability(manifest.capabilities, required)

    def get_capabilities(self, plugin_name: str) -> Set[str]:
        """Get expanded capabilities for a plugin."""
        manifest = self._manifests.get(plugin_name)
        if manifest is None:
            return set()
        return self._cap_graph.expand(manifest.capabilities)

    def missing_capabilities(self, plugin_name: str, *required: str) -> Set[str]:
        """Get capabilities that a plugin is missing."""
        manifest = self._manifests.get(plugin_name)
        if manifest is None:
            return set(required)
        return self._cap_graph.missing(manifest.capabilities, *required)

    # ── Resource Tracking ─────────────────────────────────

    def record_api_call(self, plugin_name: str) -> bool:
        """Record an API call. Returns False if limit exceeded."""
        usage = self._usage.get(plugin_name)
        manifest = self._manifests.get(plugin_name)
        if not usage or not manifest:
            return False

        usage.reset_counters_if_needed()
        usage.api_calls += 1

        if usage.api_calls > manifest.resource_limits.api_calls_per_minute:
            usage.violations += 1
            logger.warning(
                f"PluginSandbox: '{plugin_name}' exceeded API rate limit "
                f"({usage.api_calls}/{manifest.resource_limits.api_calls_per_minute}/min)"
            )
            if usage.violations >= 3:
                usage.is_suspended = True
                logger.error(f"PluginSandbox: SUSPENDED '{plugin_name}' (3+ violations)")
            return False
        return True

    def record_event(self, plugin_name: str) -> bool:
        """Record an event publication. Returns False if limit exceeded."""
        usage = self._usage.get(plugin_name)
        manifest = self._manifests.get(plugin_name)
        if not usage or not manifest:
            return False

        usage.reset_counters_if_needed()
        usage.events_published += 1

        if usage.events_published > manifest.resource_limits.event_rate_per_second:
            usage.violations += 1
            logger.warning(
                f"PluginSandbox: '{plugin_name}' exceeded event rate limit"
            )
            return False
        return True

    def is_suspended(self, plugin_name: str) -> bool:
        """Check if a plugin has been suspended for violations."""
        usage = self._usage.get(plugin_name)
        return usage.is_suspended if usage else False

    def reinstate(self, plugin_name: str) -> None:
        """Manually reinstate a suspended plugin."""
        usage = self._usage.get(plugin_name)
        if usage:
            usage.is_suspended = False
            usage.violations = 0
            logger.info(f"PluginSandbox: reinstated '{plugin_name}'")

    # ── Introspection ─────────────────────────────────────

    def get_usage(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get current resource usage for a plugin."""
        usage = self._usage.get(plugin_name)
        manifest = self._manifests.get(plugin_name)
        if not usage or not manifest:
            return None

        return {
            "api_calls": usage.api_calls,
            "api_limit": manifest.resource_limits.api_calls_per_minute,
            "events_published": usage.events_published,
            "event_limit": manifest.resource_limits.event_rate_per_second,
            "violations": usage.violations,
            "is_suspended": usage.is_suspended,
        }

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all sandboxed plugins with their status."""
        result = []
        for name, manifest in self._manifests.items():
            usage = self._usage.get(name)
            result.append({
                "name": name,
                "version": manifest.version,
                "capabilities": sorted(manifest.capabilities),
                "trusted": manifest.trusted,
                "suspended": usage.is_suspended if usage else False,
                "violations": usage.violations if usage else 0,
            })
        return result
