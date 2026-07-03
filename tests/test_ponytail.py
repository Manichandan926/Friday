import pytest
from app.agents.skills.ponytail import inject_ponytail, PONYTAIL_CORE

def test_inject_full_mode():
    prompt = "Hello Agent"
    res = inject_ponytail(prompt, "full")
    assert PONYTAIL_CORE in res
    assert "PONYTAIL LEVEL: FULL" in res
    assert res.endswith(prompt)

def test_inject_lite_mode():
    prompt = "Hello Agent"
    res = inject_ponytail(prompt, "lite")
    assert PONYTAIL_CORE in res
    assert "PONYTAIL LEVEL: LITE" in res
    assert res.endswith(prompt)

def test_inject_ultra_mode():
    prompt = "Hello Agent"
    res = inject_ponytail(prompt, "ultra")
    assert PONYTAIL_CORE in res
    assert "PONYTAIL LEVEL: ULTRA" in res
    assert res.endswith(prompt)

def test_inject_off_mode():
    prompt = "Hello Agent"
    res = inject_ponytail(prompt, "off")
    assert res == prompt

def test_all_agents_import_ponytail():
    # Verify that the agent files import and contain ponytail integration references
    agent_paths = [
        "app/agents/inbox_agent.py",
        "app/agents/memory_agent.py",
        "app/agents/placement_agent.py",
        "app/agents/planner_agent.py",
        "app/agents/shell_agent.py",
        "app/agents/task_agent.py",
        # app/core/assistant.py deliberately excluded: ponytail is a coding
        # discipline for the worker agents, not FRIDAY's companion chat voice.
    ]
    
    for path in agent_paths:
        import os
        assert os.path.exists(path), f"Agent path {path} does not exist"
        with open(path, "r") as f:
            content = f.read()
            assert "inject_ponytail" in content, f"Agent {path} does not import or refer to inject_ponytail"
