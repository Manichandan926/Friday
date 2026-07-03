import pytest
from app.core.capability_graph import CapabilityGraph, CapabilityNode

def test_graph_cycle_detection():
    graph = CapabilityGraph()
    # By default, a tree has no cycles
    assert graph.detect_cycle() is False
    
    # Let's manually introduce a cycle in the graph
    # email node
    email_node = graph._nodes["email"]
    # Let's make email a child of email.read (creating a cycle)
    email_read_node = graph._nodes["email.read"]
    email_read_node.children["email"] = email_node
    
    assert graph.detect_cycle() is True

def test_hierarchical_expansion():
    graph = CapabilityGraph()
    
    # Granting 'email' should expand to cover read, send, delete
    granted = {"email"}
    expanded = graph.expand(granted)
    assert "email" in expanded
    assert "email.read" in expanded
    assert "email.send" in expanded
    assert "email.delete" in expanded
    
    # Granting 'email.read' should NOT expand to cover 'email.send'
    granted_sub = {"email.read"}
    expanded_sub = graph.expand(granted_sub)
    assert "email.read" in expanded_sub
    assert "email.send" not in expanded_sub

def test_missing_capability():
    graph = CapabilityGraph()
    
    granted = {"email.read", "tasks"}
    
    # Check what required capabilities are missing
    missing = graph.missing(granted, "email.send", "tasks.read", "device.control")
    
    # 'email.send' is missing because we only have 'email.read'
    assert "email.send" in missing
    # 'tasks.read' is NOT missing because we have parent 'tasks' granted
    assert "tasks.read" not in missing
    # 'device.control' is missing because we don't have device capability
    assert "device.control" in missing

def test_deep_hierarchy_capability():
    graph = CapabilityGraph()
    
    # Add a deeply nested capability for testing
    graph._add("device.control.light")
    
    # Granting 'device' should cover 'device.control.light'
    assert graph.has_capability({"device"}, "device.control.light") is True
    
    # Granting 'device.control' should cover 'device.control.light'
    assert graph.has_capability({"device.control"}, "device.control.light") is True
    
    # Granting 'device.control.light' should NOT cover 'device.control'
    assert graph.has_capability({"device.control.light"}, "device.control") is False
