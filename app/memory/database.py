from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from app.core.config import settings
from app.core.logger import logger
from app.memory.models import Base

# SQLite multi-threading config
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False  # set to True for SQL trace
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)

def _migrate() -> None:
    """Additive column migrations for pre-existing databases.

    create_all() creates missing tables but never adds columns to tables
    that already exist, so new columns land here as idempotent ALTERs.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "conversations" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("conversations")}
    additions = {
        "summary": "ALTER TABLE conversations ADD COLUMN summary TEXT",
        "summary_until_id": "ALTER TABLE conversations ADD COLUMN summary_until_id INTEGER",
    }
    with engine.begin() as conn:
        for column, ddl in additions.items():
            if column not in existing:
                logger.info(f"Migrating: adding conversations.{column}")
                conn.execute(text(ddl))

def init_db() -> None:
    """Create DB schema."""
    try:
        logger.info("Initializing SQLite database...")
        Base.metadata.create_all(bind=engine)
        _migrate()
        logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

@contextmanager
def get_db_session():
    """Context manager for session lifecycle handling."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database transaction error, rolled back: {e}")
        raise
    finally:
        session.close()
