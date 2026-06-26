"""
permissions.py — Permission System for FRIDAY.

Controls what capabilities agents and plugins can access.
Works in conjunction with the Policy Engine to enforce
the principle of least privilege across all FRIDAY components.

Architecture:
    Agent/Plugin declares capabilities → PermissionSystem checks → Allow/Deny
"""

from __future__ import annotations

from enum import unique, Enum
from typing import Dict, FrozenSet, Optional, Set

from app.core.logger import logger
from app.core.plugin import Capability


@unique
class Permission(str, Enum):
    """Fine-grained permission flags."""
    READ            = "read"
    WRITE           = "write"
    EXECUTE         = "execute"
    NETWORK         = "network"
    CAMERA          = "camera"
    MICROPHONE      = "microphone"
    DEVICE_CONTROL  = "device_control"
    SECURITY_CONTROL = "security_control"
    FILE_READ       = "file_read"
    FILE_WRITE      = "file_write"
    NOTIFICATIONS   = "notifications"
    DATABASE        = "database"


# Default permission grants per role
_DEFAULT_ROLE_PERMISSIONS: Dict[str, FrozenSet[Permission]] = {
    "owner": frozenset(Permission),  # owner gets everything
    "agent": frozenset({
        Permission.READ,
        Permission.EXECUTE,
        Permission.NETWORK,
        Permission.NOTIFICATIONS,
        Permission.DATABASE,
    }),
    "plugin": frozenset({
        Permission.READ,
        Permission.EXECUTE,
        Permission.NOTIFICATIONS,
    }),
    "guest": frozenset({
        Permission.READ,
    }),
}

# Mapping from Capability to required Permissions
_CAPABILITY_PERMISSIONS: Dict[Capability, FrozenSet[Permission]] = {
    Capability.EMAIL:          frozenset({Permission.READ, Permission.NETWORK}),
    Capability.TASKS:          frozenset({Permission.READ, Permission.WRITE, Permission.DATABASE}),
    Capability.PROJECTS:       frozenset({Permission.READ, Permission.WRITE, Permission.DATABASE}),
    Capability.KNOWLEDGE:      frozenset({Permission.READ, Permission.WRITE, Permission.DATABASE}),
    Capability.MEMORY:         frozenset({Permission.READ, Permission.WRITE, Permission.DATABASE}),
    Capability.SHELL:          frozenset({Permission.EXECUTE}),
    Capability.NETWORK:        frozenset({Permission.NETWORK}),
    Capability.HOME_CONTROL:   frozenset({Permission.DEVICE_CONTROL}),
    Capability.SECURITY:       frozenset({Permission.SECURITY_CONTROL}),
    Capability.CAMERA:         frozenset({Permission.CAMERA}),
    Capability.MICROPHONE:     frozenset({Permission.MICROPHONE}),
    Capability.DEVICE_CONTROL: frozenset({Permission.DEVICE_CONTROL}),
    Capability.NOTIFICATIONS:  frozenset({Permission.NOTIFICATIONS}),
    Capability.SYSTEM_INFO:    frozenset({Permission.READ}),
    Capability.FILE_READ:      frozenset({Permission.FILE_READ}),
    Capability.FILE_WRITE:     frozenset({Permission.FILE_WRITE}),
}


class PermissionSystem:
    """Manages and checks permissions for agents, plugins, and roles.

    Usage::

        ps = PermissionSystem()
        ps.grant("my_plugin", Permission.READ, Permission.NETWORK)
        ps.check("my_plugin", Permission.READ)  # True
        ps.check("my_plugin", Permission.CAMERA) # False
    """

    def __init__(self):
        # entity_name → set of granted permissions
        self._grants: Dict[str, Set[Permission]] = {}

    def grant(self, entity: str, *permissions: Permission) -> None:
        """Grant permissions to an entity (agent, plugin, or role)."""
        if entity not in self._grants:
            self._grants[entity] = set()
        self._grants[entity].update(permissions)
        logger.debug(
            f"PermissionSystem: granted {[p.value for p in permissions]} to '{entity}'"
        )

    def revoke(self, entity: str, *permissions: Permission) -> None:
        """Revoke permissions from an entity."""
        if entity in self._grants:
            self._grants[entity] -= set(permissions)
            logger.debug(
                f"PermissionSystem: revoked {[p.value for p in permissions]} from '{entity}'"
            )

    def check(self, entity: str, permission: Permission) -> bool:
        """Check if an entity has a specific permission."""
        granted = self._grants.get(entity, set())
        return permission in granted

    def check_all(self, entity: str, *permissions: Permission) -> bool:
        """Check if an entity has ALL of the specified permissions."""
        granted = self._grants.get(entity, set())
        return all(p in granted for p in permissions)

    def check_any(self, entity: str, *permissions: Permission) -> bool:
        """Check if an entity has ANY of the specified permissions."""
        granted = self._grants.get(entity, set())
        return any(p in granted for p in permissions)

    def grant_role_defaults(self, entity: str, role: str) -> None:
        """Grant the default permission set for a role."""
        defaults = _DEFAULT_ROLE_PERMISSIONS.get(role, frozenset())
        self.grant(entity, *defaults)

    def grant_for_capabilities(self, entity: str, capabilities: list[Capability]) -> None:
        """Automatically grant required permissions based on declared capabilities.

        Called by PluginManager when a plugin is loaded to ensure
        it gets exactly the permissions it needs (and no more).
        """
        for cap in capabilities:
            required = _CAPABILITY_PERMISSIONS.get(cap, frozenset())
            self.grant(entity, *required)

    def get_permissions(self, entity: str) -> Set[Permission]:
        """Get all permissions granted to an entity."""
        return self._grants.get(entity, set()).copy()

    def list_entities(self) -> Dict[str, list]:
        """List all entities and their permissions."""
        return {
            entity: [p.value for p in perms]
            for entity, perms in self._grants.items()
        }

    def enforce(self, entity: str, permission: Permission) -> None:
        """Check permission and raise PermissionError if denied."""
        if not self.check(entity, permission):
            raise PermissionError(
                f"Entity '{entity}' does not have '{permission.value}' permission"
            )
