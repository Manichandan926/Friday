import pytest
from datetime import datetime, timezone
from app.memory.database import init_db, get_db_session
from app.memory.memory_manager import MemoryManager
from app.memory.models import Base

def test_init_db_creates_tables():
    # Calling init_db should run without error and ensure tables exist
    init_db()
    with get_db_session() as session:
        # Check that we can query from tables mapped by declarative Base
        for mapper in Base.registry.mappers:
            model_class = mapper.class_
            # Just do a simple count query to verify tables exist
            session.query(model_class).count()

def test_add_and_get_conversation():
    # 1. Create a conversation
    conv1 = MemoryManager.create_conversation("Sprint Discussion")
    assert conv1.id is not None
    assert conv1.title == "Sprint Discussion"
    
    # 2. Get conversations and assert presence
    convs = MemoryManager.get_conversations()
    titles = [c.title for c in convs]
    assert "Sprint Discussion" in titles

def test_add_and_get_messages():
    conv = MemoryManager.create_conversation("Chat Log")
    
    # 1. Add messages
    msg1 = MemoryManager.add_message(conv.id, "user", "Hello FRIDAY")
    msg2 = MemoryManager.add_message(conv.id, "assistant", "Hello user")
    
    # 2. Get messages and check order (ascending created_at)
    msgs = MemoryManager.get_messages(conv.id)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "Hello FRIDAY"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == "Hello user"

def test_add_and_get_tasks():
    due = datetime(2026, 7, 1, 12, 0)
    
    # 1. Add task
    t = MemoryManager.add_task(
        title="Implement database tests",
        description="Write unit tests for models and memory manager",
        priority="high",
        due_date=due
    )
    assert t.id is not None
    assert t.status == "pending"
    
    # 2. Retrieve tasks
    pending = MemoryManager.get_tasks(status="pending")
    titles = [tk.title for tk in pending]
    assert "Implement database tests" in titles
    
    # 3. Update task status
    updated = MemoryManager.update_task_status(t.id, "completed")
    assert updated is not None
    assert updated.status == "completed"
    
    # Verify in query
    completed = MemoryManager.get_tasks(status="completed")
    assert any(tk.id == t.id for tk in completed)

def test_add_and_get_memory_items():
    # 1. Add memory item
    item = MemoryManager.add_memory_item("user_preferences", "Prefers dark mode")
    assert item.id is not None
    
    # 2. Get memory items
    items = MemoryManager.get_memory_items(category="user_preferences")
    contents = [m.content for m in items]
    assert "Prefers dark mode" in contents
    
    # 3. Delete memory item
    success = MemoryManager.delete_memory_item(item.id)
    assert success is True
    
    # Double check deletion
    remaining = MemoryManager.get_memory_items(category="user_preferences")
    assert not any(m.id == item.id for m in remaining)

def test_get_memory_items_respects_limit():
    # Scale guard: the recall/dedup hot paths pass a limit so per-turn cost
    # stays flat as the store grows. The limit must bound the result and
    # return newest-first.
    conv_cat = "limit_probe"
    for i in range(25):
        MemoryManager.add_memory_item(conv_cat, f"fact number {i}")
    capped = MemoryManager.get_memory_items(category=conv_cat, limit=5)
    assert len(capped) == 5
    # newest-first: the last-inserted fact is present, an early one is not
    contents = [m.content for m in capped]
    assert "fact number 24" in contents
    assert "fact number 0" not in contents


def test_get_recent_messages_is_bounded_tail_in_order():
    # memory_agent needs only the last few messages; this must fetch a bounded
    # tail (not the whole conversation) and return it chronologically.
    conv = MemoryManager.create_conversation("tail probe")
    for i in range(20):
        MemoryManager.add_message(conv.id, "user", f"m{i}")
    tail = MemoryManager.get_recent_messages(conv.id, 4)
    assert [m.content for m in tail] == ["m16", "m17", "m18", "m19"]


def test_add_and_get_knowledge():
    # 1. Add knowledge item
    k = MemoryManager.add_knowledge_item(
        title="Dijkstra Algorithm",
        category="dsa",
        content="Shortest path algorithm for weighted graphs.",
        tags="graph,pathfinding"
    )
    assert k.id is not None
    
    # 2. Get knowledge items
    items = MemoryManager.get_knowledge_items(category="dsa")
    titles = [item.title for item in items]
    assert "Dijkstra Algorithm" in titles
    
    # 3. Delete knowledge item
    deleted = MemoryManager.delete_knowledge_item(k.id)
    assert deleted is True

def test_search_knowledge():
    MemoryManager.add_knowledge_item(
        title="AWS DynamoDB Deep Dive",
        category="aws",
        content="Key-value and document database delivering single-digit millisecond performance.",
        tags="cloud,database,nosql"
    )
    
    # Search by title keyword
    results_title = MemoryManager.search_knowledge("DynamoDB")
    assert len(results_title) >= 1
    assert results_title[0].title == "AWS DynamoDB Deep Dive"
    
    # Search by content keyword
    results_content = MemoryManager.search_knowledge("millisecond")
    assert len(results_content) >= 1
    
    # Search by tag keyword
    results_tag = MemoryManager.search_knowledge("nosql")
    assert len(results_tag) >= 1

def test_add_and_get_emails():
    received = datetime(2026, 6, 27, 9, 30)
    
    # 1. Save email
    email = MemoryManager.save_email(
        email_id="msg-gmail-abc123xyz",
        subject="GitHub Security Alert",
        sender="noreply@github.com",
        body_summary="Sec alert detected on repo",
        received_at=received,
        priority="high"
    )
    assert email.id == "msg-gmail-abc123xyz"
    assert email.is_processed is False
    
    # 2. Get emails
    emails = MemoryManager.get_emails()
    ids = [e.id for e in emails]
    assert "msg-gmail-abc123xyz" in ids

def test_add_and_get_applications():
    deadline = datetime(2026, 7, 15, 17, 0)
    
    # 1. Add application
    app = MemoryManager.add_application(
        company="Paytm",
        role="Product Engineer",
        status="applied",
        deadline=deadline,
        source_email_id="msg-gmail-abc123xyz"
    )
    assert app.id is not None
    
    # 2. Get applications
    apps = MemoryManager.get_applications(status="applied")
    companies = [a.company for a in apps]
    assert "Paytm" in companies
    
    # 3. Update status
    updated = MemoryManager.update_application_status(app.id, "interviewing")
    assert updated is not None
    assert updated.status == "interviewing"

def test_notifications():
    # 1. Add notification
    notif = MemoryManager.add_notification(
        title="High CPU Alert",
        message="System CPU usage is at 92%.",
        category="system"
    )
    assert notif.id is not None
    assert notif.is_read is False
    
    # 2. Get notifications (unread only)
    unread = MemoryManager.get_notifications(unread_only=True)
    assert any(n.id == notif.id for n in unread)
    
    # 3. Mark notifications read
    count = MemoryManager.mark_notifications_read()
    assert count >= 1
    
    # Verify unread list is empty or doesn't contain this notification
    unread_after = MemoryManager.get_notifications(unread_only=True)
    assert not any(n.id == notif.id for n in unread_after)
