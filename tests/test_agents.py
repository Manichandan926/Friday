import pytest
import json
from unittest.mock import patch
from app.agents.shell_agent import ShellAgent
from app.agents.task_agent import TaskAgent
from app.agents.memory_agent import MemoryAgent
from app.agents.inbox_agent import InboxAgent
from app.agents.placement_agent import PlacementAgent
from app.agents.planner_agent import PlannerAgent
from app.llm.provider import LLMProvider
from app.memory.memory_manager import MemoryManager
from app.memory.database import get_db_session
from app.memory.models import Task, MemoryItem, Application, Conversation, Message

class MockLLMProvider(LLMProvider):
    def __init__(self):
        self.response = ""

    async def generate(self, messages, **kwargs) -> str:
        return self.response

@pytest.fixture
def mock_provider():
    provider = MockLLMProvider()
    with patch("app.agents.shell_agent.get_llm_provider", return_value=provider), \
         patch("app.agents.task_agent.get_llm_provider", return_value=provider), \
         patch("app.agents.memory_agent.get_llm_provider", return_value=provider), \
         patch("app.agents.inbox_agent.get_llm_provider", return_value=provider), \
         patch("app.agents.placement_agent.get_llm_provider", return_value=provider), \
         patch("app.agents.planner_agent.get_llm_provider", return_value=provider):
        yield provider

@pytest.mark.asyncio
async def test_shell_agent_generates_command(mock_provider):
    mock_provider.response = '{"command": "pwd", "explanation": "Show current directory"}'
    agent = ShellAgent()
    res = await agent.answer_with_shell("Where am I?")
    
    assert "pwd" in res
    assert "Show current directory" in res

@pytest.mark.asyncio
async def test_shell_agent_blocks_unsafe(mock_provider):
    mock_provider.response = '{"command": "sudo rm -rf /", "explanation": "Destroy everything"}'
    agent = ShellAgent()
    res = await agent.answer_with_shell("Clean disk")
    
    assert "Command blocked for safety" in res

@pytest.mark.asyncio
async def test_task_agent_parses_tasks(mock_provider):
    mock_provider.response = json.dumps({
        "tasks": [
            {
                "title": "Task Agent Spec Test",
                "description": "Verify task parsing",
                "priority": "high",
                "days_from_now": 3
            }
        ]
    })
    
    agent = TaskAgent()
    created = await agent.generate_tasks_from_plan("Create task test")
    
    assert len(created) == 1
    assert created[0]["title"] == "Task Agent Spec Test"
    assert created[0]["priority"] == "high"
    
    # Check database record
    with get_db_session() as session:
        record = session.query(Task).filter_by(title="Task Agent Spec Test").first()
        assert record is not None
        assert record.description == "Verify task parsing"

@pytest.mark.asyncio
async def test_memory_agent_extracts_facts(mock_provider):
    # Setup a mock conversation in DB
    conv = MemoryManager.create_conversation()
    MemoryManager.add_message(conv.id, "user", "I am studying at KL University.")
    MemoryManager.add_message(conv.id, "assistant", "That is great.")
    
    mock_provider.response = json.dumps({
        "new_memories": [
            {
                "category": "user_info",
                "content": "Studying at KL University"
            }
        ]
    })
    
    agent = MemoryAgent()
    saved_count = await agent.extract_and_save_memories(conv.id)
    
    assert saved_count == 1
    
    # Check DB
    items = MemoryManager.get_memory_items()
    contents = {m.content for m in items}
    assert "Studying at KL University" in contents

@pytest.mark.asyncio
async def test_memory_agent_deduplication(mock_provider):
    conv = MemoryManager.create_conversation()
    MemoryManager.add_message(conv.id, "user", "I live in Hyderabad.")
    
    # Add memory item directly first
    MemoryManager.add_memory_item(category="user_info", content="Lives in Hyderabad")
    
    mock_provider.response = json.dumps({
        "new_memories": [
            {
                "category": "user_info",
                "content": "Lives in Hyderabad"
            }
        ]
    })
    
    agent = MemoryAgent()
    saved_count = await agent.extract_and_save_memories(conv.id)
    
    # Should not save duplicate memory item
    assert saved_count == 0

@pytest.mark.asyncio
async def test_inbox_agent_returns_metadata(mock_provider):
    mock_provider.response = json.dumps({
        "summary": "Invitation to Paytm interview",
        "category": "interview",
        "priority": "high",
        "action_items": ["Prepare for Paytm interview"],
        "deadlines": ["2026-07-01 10:00"]
    })
    
    agent = InboxAgent()
    res = await agent.analyze_email("hr@paytm.com", "Interview Invite", "Please attend on July 1st")
    
    assert res is not None
    assert res["summary"] == "Invitation to Paytm interview"
    assert res["category"] == "interview"
    assert res["priority"] == "high"
    assert "Prepare for Paytm interview" in res["action_items"]

@pytest.mark.asyncio
async def test_placement_agent_tracks_application(mock_provider):
    mock_provider.response = json.dumps({
        "company": "Paytm",
        "role": "QA Engineer Intern",
        "status": "interviewing",
        "days_until_deadline": 4
    })
    
    agent = PlacementAgent()
    app_data = await agent.parse_and_save_application(
        sender="careers@paytm.com",
        subject="Interview scheduled",
        body="QA Intern interview on Tuesday",
        source_email_id="msg-paytm-qa-123"
    )
    
    assert app_data is not None
    assert app_data["company"] == "Paytm"
    assert app_data["role"] == "QA Engineer Intern"
    assert app_data["status"] == "interviewing"
    
    # Check DB
    apps = MemoryManager.get_applications()
    match = [a for a in apps if a.company == "Paytm" and a.role == "QA Engineer Intern"]
    assert len(match) == 1
    assert match[0].status == "interviewing"

@pytest.mark.asyncio
async def test_planner_agent_generates_briefing(mock_provider):
    mock_provider.response = "# Daily Briefing\nGood morning Mani. You have 3 tasks today."
    
    agent = PlannerAgent()
    briefing = await agent.generate_daily_briefing()
    
    assert "Daily Briefing" in briefing
    assert "Good morning Mani" in briefing
