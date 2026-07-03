import pytest
import time
from app.core.plugin_sandbox import PluginSandbox, PluginManifest, ResourceLimits
from app.core.capability_graph import CapabilityGraph

def test_manifest_validation():
    sandbox = PluginSandbox()
    
    # 1. Valid Manifest
    valid_manifest = PluginManifest(
        name="test_plugin",
        version="1.0.0",
        capabilities={"email.read", "tasks.write"},
        resource_limits=ResourceLimits(cpu_percent=50, memory_mb=256)
    )
    errors = sandbox.validate_manifest(valid_manifest)
    assert len(errors) == 0
    
    # 2. Invalid Manifest: empty name, unknown capability, invalid resource limits
    invalid_manifest = PluginManifest(
        name="",
        version="",
        capabilities={"unknown.cap"},
        resource_limits=ResourceLimits(cpu_percent=150, memory_mb=-10)
    )
    errors = sandbox.validate_manifest(invalid_manifest)
    assert "Plugin name is required" in errors
    assert "Plugin version is required" in errors
    assert "Unknown capability: 'unknown.cap'" in errors
    assert "Invalid CPU limit: 150%" in errors
    assert "Invalid memory limit: -10MB" in errors

def test_capability_enforcement():
    sandbox = PluginSandbox()
    manifest = PluginManifest(
        name="email_plugin",
        version="1.0.0",
        capabilities={"email.read"}
    )
    sandbox.register(manifest)
    
    # Check allowed capability
    assert sandbox.check_capability("email_plugin", "email.read") is True
    
    # Check denied capability (not in manifest)
    assert sandbox.check_capability("email_plugin", "email.send") is False
    
    # Check hierarchical capability extension (email.read covers nothing higher than itself, but "email" covers "email.read")
    manifest_hier = PluginManifest(
        name="admin_plugin",
        version="1.0.0",
        capabilities={"device"}
    )
    sandbox.register(manifest_hier)
    assert sandbox.check_capability("admin_plugin", "device.control") is True
    assert sandbox.check_capability("admin_plugin", "device.read") is True
    assert sandbox.check_capability("admin_plugin", "device.admin") is True

def test_resource_rate_limiting():
    sandbox = PluginSandbox()
    manifest = PluginManifest(
        name="chatty_plugin",
        version="1.0.0",
        resource_limits=ResourceLimits(
            api_calls_per_minute=2,
            event_rate_per_second=2
        )
    )
    sandbox.register(manifest)
    
    # First 2 API calls are fine
    assert sandbox.record_api_call("chatty_plugin") is True
    assert sandbox.record_api_call("chatty_plugin") is True
    # 3rd is rate limited
    assert sandbox.record_api_call("chatty_plugin") is False
    
    # Events
    assert sandbox.record_event("chatty_plugin") is True
    assert sandbox.record_event("chatty_plugin") is True
    assert sandbox.record_event("chatty_plugin") is False

def test_sandbox_suspension():
    sandbox = PluginSandbox()
    manifest = PluginManifest(
        name="bad_plugin",
        version="1.0.0",
        resource_limits=ResourceLimits(api_calls_per_minute=1)
    )
    sandbox.register(manifest)
    
    # Limit is 1. Record multiple calls to trigger violations.
    # 1st call: fine (1/1)
    sandbox.record_api_call("bad_plugin")
    
    # 2nd call: violation 1
    sandbox.record_api_call("bad_plugin")
    assert sandbox.is_suspended("bad_plugin") is False
    
    # 3rd call: violation 2
    sandbox.record_api_call("bad_plugin")
    assert sandbox.is_suspended("bad_plugin") is False
    
    # 4th call: violation 3 -> suspended!
    sandbox.record_api_call("bad_plugin")
    assert sandbox.is_suspended("bad_plugin") is True

def test_plugin_reinstate():
    sandbox = PluginSandbox()
    manifest = PluginManifest(
        name="reinstated_plugin",
        version="1.0.0",
        resource_limits=ResourceLimits(api_calls_per_minute=1)
    )
    sandbox.register(manifest)
    
    # Induce 3 violations to suspend it
    sandbox.record_api_call("reinstated_plugin") # 1/1
    sandbox.record_api_call("reinstated_plugin") # violation 1
    sandbox.record_api_call("reinstated_plugin") # violation 2
    sandbox.record_api_call("reinstated_plugin") # violation 3 -> suspended
    assert sandbox.is_suspended("reinstated_plugin") is True
    
    # Reinstate
    sandbox.reinstate("reinstated_plugin")
    assert sandbox.is_suspended("reinstated_plugin") is False
    
    # Check usage resets
    usage = sandbox.get_usage("reinstated_plugin")
    assert usage["violations"] == 0
    assert usage["is_suspended"] is False
