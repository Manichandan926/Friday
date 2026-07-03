import ast
import os
import pytest

def _parse_stub(file_path):
    assert os.path.exists(file_path), f"Stub path {file_path} does not exist"
    with open(file_path, "r") as f:
        content = f.read()
    # Check syntax validation (test 19.4 / syntax validity)
    tree = ast.parse(content, filename=file_path)
    return tree

def _get_class_methods(tree, class_name):
    methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
            return methods
    return None

def test_event_runtime_pyi_exists():
    path = "app/core/runtime_interfaces/event_runtime.pyi"
    tree = _parse_stub(path)
    methods = _get_class_methods(tree, "EventRuntime")
    assert methods is not None, "EventRuntime class not found in stub"
    
    expected_methods = ["start", "stop", "publish", "publish_sync", "subscribe", "get_recent_events"]
    for method in expected_methods:
        assert method in methods, f"Method {method} not found in EventRuntime stub"

def test_actor_runtime_pyi_exists():
    path = "app/core/runtime_interfaces/actor_runtime.pyi"
    tree = _parse_stub(path)
    methods = _get_class_methods(tree, "ActorRuntime")
    assert methods is not None, "ActorRuntime class not found in stub"
    
    expected_methods = ["start_all", "stop_all", "spawn", "get_actor", "send"]
    for method in expected_methods:
        assert method in methods, f"Method {method} not found in ActorRuntime stub"

def test_transport_runtime_pyi_exists():
    path = "app/core/runtime_interfaces/transport_runtime.pyi"
    tree = _parse_stub(path)
    methods = _get_class_methods(tree, "TransportRuntime")
    assert methods is not None, "TransportRuntime class not found in stub"
    
    expected_methods = ["connect", "disconnect", "publish", "subscribe"]
    for method in expected_methods:
        assert method in methods, f"Method {method} not found in TransportRuntime stub"
