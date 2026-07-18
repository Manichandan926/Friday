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

def ensure_knowledge_fts() -> None:
    """Create the FTS5 full-text index over knowledge_items (+ triggers that
    keep it in sync), and backfill existing rows, if it isn't there yet.

    Idempotent: does nothing once the index exists. Degrades gracefully — if
    this SQLite build lacks the FTS5 module, we log and skip, and
    MemoryManager.search_knowledge falls back to a LIKE substring search.
    Kept out of Base.metadata deliberately: an FTS5 virtual table + triggers is
    raw SQLite DDL SQLAlchemy's create_all() doesn't model. tags can be NULL,
    so every read of it is COALESCE'd to '' to keep the index consistent
    (a mismatch between insert/delete values corrupts an external-content FTS).
    """
    from sqlalchemy import inspect, text

    if not str(engine.url).startswith("sqlite"):
        return
    if "knowledge_items" not in inspect(engine).get_table_names():
        return
    with engine.begin() as conn:
        already = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        )).first()
        if already:
            return
        try:
            conn.execute(text(
                "CREATE VIRTUAL TABLE knowledge_fts USING fts5("
                "title, content, tags, content='knowledge_items', content_rowid='id')"
            ))
        except Exception as e:  # FTS5 module missing in this SQLite build
            logger.warning(f"FTS5 unavailable — knowledge search will use LIKE fallback: {e}")
            return
        conn.execute(text(
            "CREATE TRIGGER knowledge_fts_ai AFTER INSERT ON knowledge_items BEGIN "
            "INSERT INTO knowledge_fts(rowid, title, content, tags) "
            "VALUES (new.id, new.title, new.content, COALESCE(new.tags,'')); END"))
        conn.execute(text(
            "CREATE TRIGGER knowledge_fts_ad AFTER DELETE ON knowledge_items BEGIN "
            "INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags) "
            "VALUES('delete', old.id, old.title, old.content, COALESCE(old.tags,'')); END"))
        conn.execute(text(
            "CREATE TRIGGER knowledge_fts_au AFTER UPDATE ON knowledge_items BEGIN "
            "INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags) "
            "VALUES('delete', old.id, old.title, old.content, COALESCE(old.tags,'')); "
            "INSERT INTO knowledge_fts(rowid, title, content, tags) "
            "VALUES (new.id, new.title, new.content, COALESCE(new.tags,'')); END"))
        conn.execute(text(
            "INSERT INTO knowledge_fts(rowid, title, content, tags) "
            "SELECT id, title, content, COALESCE(tags,'') FROM knowledge_items"))
        logger.info("Created knowledge_fts FTS5 index + sync triggers.")


def init_db() -> None:
    """Create DB schema."""
    try:
        logger.info("Initializing SQLite database...")
        Base.metadata.create_all(bind=engine)
        _migrate()
        ensure_knowledge_fts()
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
