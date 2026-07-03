"""
capability_graph.py — Hierarchical Capability System for FRIDAY.

Instead of flat capabilities (EMAIL, TASKS), this provides a tree structure:

    EMAIL
    ├── READ
    ├── SEND
    └── DELETE

    DEVICE
    ├── READ
    ├── CONTROL
    └── ADMIN

A plugin requesting DEVICE.READ gets read access but cannot CONTROL devices.
A plugin requesting DEVICE gets all child permissions (READ + CONTROL + ADMIN).

This becomes the security model for the entire Personal AI OS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set

from app.core.logger import logger


@dataclass
class CapabilityNode:
    """A single node in the capability tree."""
    name: str
    full_path: str                                          # e.g. "device.control"
    children: Dict[str, CapabilityNode] = field(default_factory=dict)
    parent: Optional[CapabilityNode] = field(default=None, repr=False)

    def add_child(self, name: str) -> CapabilityNode:
        """Add a child capability node."""
        child_path = f"{self.full_path}.{name}" if self.full_path else name
        child = CapabilityNode(name=name, full_path=child_path, parent=self)
        self.children[name] = child
        return child

    def has_child(self, name: str) -> bool:
        return name in self.children

    def get_child(self, name: str) -> Optional[CapabilityNode]:
        return self.children.get(name)

    def all_paths(self) -> Set[str]:
        """Get this node's path plus all descendant paths."""
        paths = {self.full_path}
        for child in self.children.values():
            paths.update(child.all_paths())
        return paths


class CapabilityGraph:
    """Hierarchical capability tree for FRIDAY.

    Usage::

        graph = CapabilityGraph()

        # Granting "email" gives read + send + delete
        graph.has_capability({"email"}, "email.read")  # True

        # Granting "email.read" does NOT give send or delete
        graph.has_capability({"email.read"}, "email.send")  # False

        # Check what a grant set expands to
        expanded = graph.expand({"device", "email.read"})
        # → {"device", "device.read", "device.control", "device.admin", "email.read"}
    """

    def __init__(self):
        self._root = CapabilityNode(name="root", full_path="")
        self._nodes: Dict[str, CapabilityNode] = {}
        self._build_default_tree()

    def _build_default_tree(self) -> None:
        """Build the default FRIDAY capability hierarchy."""

        # Email
        email = self._add("email")
        self._add("email.read")
        self._add("email.send")
        self._add("email.delete")

        # Tasks
        self._add("tasks")
        self._add("tasks.read")
        self._add("tasks.write")
        self._add("tasks.delete")

        # Projects
        self._add("projects")
        self._add("projects.read")
        self._add("projects.write")

        # Knowledge
        self._add("knowledge")
        self._add("knowledge.read")
        self._add("knowledge.write")
        self._add("knowledge.delete")

        # Memory
        self._add("memory")
        self._add("memory.read")
        self._add("memory.write")

        # Shell
        self._add("shell")
        self._add("shell.read_only")
        self._add("shell.execute")

        # Network
        self._add("network")
        self._add("network.local")
        self._add("network.external")

        # Device
        self._add("device")
        self._add("device.read")
        self._add("device.control")
        self._add("device.admin")

        # Security
        self._add("security")
        self._add("security.read")
        self._add("security.control")
        self._add("security.admin")

        # Camera
        self._add("camera")
        self._add("camera.stream")
        self._add("camera.snapshot")
        self._add("camera.detect")

        # Microphone
        self._add("microphone")
        self._add("microphone.listen")
        self._add("microphone.record")

        # System
        self._add("system")
        self._add("system.read")
        self._add("system.control")

        # Files
        self._add("files")
        self._add("files.read")
        self._add("files.write")
        self._add("files.delete")

        # Notifications
        self._add("notifications")
        self._add("notifications.read")
        self._add("notifications.send")

    def _add(self, path: str) -> CapabilityNode:
        """Add a capability by dot-separated path, creating parents as needed."""
        parts = path.split(".")
        current = self._root

        built_path = ""
        for part in parts:
            built_path = f"{built_path}.{part}" if built_path else part
            if part not in current.children:
                child = current.add_child(part)
                self._nodes[built_path] = child
            current = current.children[part]

        return current

    def detect_cycle(self) -> bool:
        """Detect if the capability graph has any cyclic dependencies using DFS.
        
        Returns True if a cycle is detected, False otherwise.
        """
        visited = set()
        stack = set()
        
        def dfs(node: CapabilityNode) -> bool:
            if node.full_path in stack:
                return True
            if node.full_path in visited:
                return False
                
            visited.add(node.full_path)
            stack.add(node.full_path)
            
            for child in node.children.values():
                if dfs(child):
                    return True
                    
            stack.remove(node.full_path)
            return False
            
        return dfs(self._root)

    # ── Queries ───────────────────────────────────────────

    def expand(self, granted: Set[str]) -> Set[str]:
        """Expand a set of granted capabilities to include all children.

        If "device" is granted, the expansion includes:
        {"device", "device.read", "device.control", "device.admin"}
        """
        expanded: Set[str] = set()
        for cap in granted:
            node = self._nodes.get(cap)
            if node:
                expanded.update(node.all_paths())
            else:
                # Unknown capability — still include it
                expanded.add(cap)
        return expanded

    def has_capability(self, granted: Set[str], required: str) -> bool:
        """Check if a required capability is covered by the granted set.

        Returns True if:
            - required is directly in granted, OR
            - a parent of required is in granted
        """
        if required in granted:
            return True

        # Check if any ancestor is granted
        parts = required.split(".")
        for i in range(1, len(parts)):
            ancestor = ".".join(parts[:i])
            if ancestor in granted:
                return True

        return False

    def check_all(self, granted: Set[str], *required: str) -> bool:
        """Check that ALL required capabilities are covered."""
        return all(self.has_capability(granted, r) for r in required)

    def check_any(self, granted: Set[str], *required: str) -> bool:
        """Check that ANY required capability is covered."""
        return any(self.has_capability(granted, r) for r in required)

    def missing(self, granted: Set[str], *required: str) -> Set[str]:
        """Return set of required capabilities not covered by granted."""
        return {r for r in required if not self.has_capability(granted, r)}

    # ── Introspection ─────────────────────────────────────

    def list_capabilities(self) -> List[str]:
        """List all registered capability paths."""
        return sorted(self._nodes.keys())

    def get_children(self, path: str) -> List[str]:
        """Get direct child capabilities of a path."""
        node = self._nodes.get(path)
        if node is None:
            return []
        return [child.full_path for child in node.children.values()]

    def get_tree(self) -> Dict[str, Any]:
        """Return the full capability tree as a nested dict."""
        def _to_dict(node: CapabilityNode) -> Dict:
            if not node.children:
                return {}
            return {
                name: _to_dict(child) for name, child in node.children.items()
            }
        return _to_dict(self._root)
