import os
import pytest
from app.core.actors.system import ActorSystem
from app.core.events.event_bus import EventBus
from app.core.metrics import MetricsRegistry
import app.memory.database
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.memory.models import Base

TEST_DB_FILE = "tests/test_friday.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_FILE}"

@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    # Remove any existing test DB file to start fresh
    if os.path.exists(TEST_DB_FILE):
        try:
            os.remove(TEST_DB_FILE)
        except Exception:
            pass
            
    # Override engine and SessionLocal to use a file-based test SQLite database
    test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    
    # Create all tables
    Base.metadata.create_all(bind=test_engine)
    
    # Store original references
    orig_engine = app.memory.database.engine
    orig_session_local = app.memory.database.SessionLocal
    
    # Override
    app.memory.database.engine = test_engine
    app.memory.database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine
    )
    
    yield
    
    # Restore original references
    app.memory.database.engine = orig_engine
    app.memory.database.SessionLocal = orig_session_local
    
    # Clean up test DB file
    if os.path.exists(TEST_DB_FILE):
        try:
            os.remove(TEST_DB_FILE)
        except Exception:
            pass

@pytest.fixture(autouse=True)
def clean_database_tables():
    # Delete all records from all tables to ensure test isolation
    with app.memory.database.get_db_session() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
    yield

@pytest.fixture(autouse=True)
def cleanup_singletons():
    # Clear ActorSystem
    sys = ActorSystem.get_instance()
    sys._actors.clear()
    sys._running = False
    
    # Clear EventBus synchronously to avoid unawaited coroutine warnings
    bus = EventBus.get_instance()
    bus._running = False
    if bus._event_available:
        bus._event_available.set()
    if bus._dispatch_task:
        bus._dispatch_task.cancel()
        
    # Reset singleton reference
    EventBus._instance = None
    
    # Clear MetricsRegistry
    metrics = MetricsRegistry.get_instance()
    metrics._metrics.clear()
    
    yield
    
    # Clear again after test
    sys._actors.clear()
    sys._running = False
    
    bus = EventBus.get_instance()
    bus._running = False
    if bus._event_available:
        bus._event_available.set()
    if bus._dispatch_task:
        bus._dispatch_task.cancel()
    EventBus._instance = None
    
    metrics._metrics.clear()
