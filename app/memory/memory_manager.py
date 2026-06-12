from datetime import datetime
from typing import List, Optional
from sqlalchemy import select
from app.memory.database import get_db_session
from app.memory.models import Conversation, Message, MemoryItem, Email, Task, Application, KnowledgeItem, Project

class MemoryManager:
    # --- Conversations ---

    @staticmethod
    def create_conversation(title: str = "New Conversation") -> Conversation:
        with get_db_session() as session:
            conv = Conversation(title=title)
            session.add(conv)
            session.flush()
            return conv

    @staticmethod
    def get_conversations() -> List[Conversation]:
        with get_db_session() as session:
            stmt = select(Conversation).order_by(Conversation.created_at.desc())
            return list(session.scalars(stmt).all())

    @staticmethod
    def add_message(conversation_id: int, role: str, content: str) -> Message:
        with get_db_session() as session:
            msg = Message(conversation_id=conversation_id, role=role, content=content)
            session.add(msg)
            session.flush()
            return msg

    @staticmethod
    def get_messages(conversation_id: int) -> List[Message]:
        with get_db_session() as session:
            stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
            return list(session.scalars(stmt).all())

    # --- Memory (Facts/Preferences) ---

    @staticmethod
    def add_memory_item(category: str, content: str) -> MemoryItem:
        with get_db_session() as session:
            item = MemoryItem(category=category, content=content)
            session.add(item)
            session.flush()
            return item

    @staticmethod
    def get_memory_items(category: Optional[str] = None) -> List[MemoryItem]:
        with get_db_session() as session:
            stmt = select(MemoryItem)
            if category:
                stmt = stmt.where(MemoryItem.category == category)
            stmt = stmt.order_by(MemoryItem.created_at.desc())
            return list(session.scalars(stmt).all())

    @staticmethod
    def delete_memory_item(item_id: int) -> bool:
        with get_db_session() as session:
            item = session.scalar(select(MemoryItem).where(MemoryItem.id == item_id))
            if item:
                session.delete(item)
                return True
            return False

    # --- Emails ---

    @staticmethod
    def save_email(email_id: str, subject: str, sender: str, body_summary: str,
                   received_at: datetime, priority: str = "medium",
                   action_items: Optional[str] = None) -> Email:
        with get_db_session() as session:
            existing = session.scalar(select(Email).where(Email.id == email_id))
            if existing:
                existing.subject = subject
                existing.sender = sender
                existing.body_summary = body_summary
                existing.received_at = received_at
                existing.priority = priority
                existing.action_items = action_items
                session.flush()
                return existing

            email = Email(
                id=email_id, subject=subject, sender=sender,
                body_summary=body_summary, received_at=received_at,
                priority=priority, action_items=action_items
            )
            session.add(email)
            session.flush()
            return email

    @staticmethod
    def get_emails(processed: Optional[bool] = None) -> List[Email]:
        with get_db_session() as session:
            stmt = select(Email)
            if processed is not None:
                stmt = stmt.where(Email.is_processed == processed)
            stmt = stmt.order_by(Email.received_at.desc())
            return list(session.scalars(stmt).all())

    # --- Tasks ---

    @staticmethod
    def add_task(title: str, description: Optional[str] = None,
                 priority: str = "medium", due_date: Optional[datetime] = None,
                 source_email_id: Optional[str] = None) -> Task:
        with get_db_session() as session:
            # dedup: skip if identical title+source already exists
            if source_email_id:
                existing = session.scalar(
                    select(Task).where(Task.title == title, Task.source_email_id == source_email_id)
                )
                if existing:
                    return existing

            task = Task(
                title=title, description=description, priority=priority,
                due_date=due_date, source_email_id=source_email_id
            )
            session.add(task)
            session.flush()
            return task

    @staticmethod
    def get_tasks(status: Optional[str] = None) -> List[Task]:
        with get_db_session() as session:
            stmt = select(Task)
            if status:
                stmt = stmt.where(Task.status == status)
            stmt = stmt.order_by(Task.created_at.desc())
            return list(session.scalars(stmt).all())

    @staticmethod
    def update_task_status(task_id: int, status: str) -> Optional[Task]:
        with get_db_session() as session:
            task = session.scalar(select(Task).where(Task.id == task_id))
            if task:
                task.status = status
                session.flush()
                return task
            return None

    # --- Placement Applications ---

    @staticmethod
    def add_application(company: str, role: str, status: str = "applied",
                        deadline: Optional[datetime] = None,
                        source_email_id: Optional[str] = None) -> Application:
        with get_db_session() as session:
            app = Application(
                company=company, role=role, status=status,
                deadline=deadline, source_email_id=source_email_id
            )
            session.add(app)
            session.flush()
            return app

    @staticmethod
    def get_applications(status: Optional[str] = None) -> List[Application]:
        with get_db_session() as session:
            stmt = select(Application)
            if status:
                stmt = stmt.where(Application.status == status)
            stmt = stmt.order_by(Application.created_at.desc())
            return list(session.scalars(stmt).all())

    @staticmethod
    def update_application_status(app_id: int, status: str) -> Optional[Application]:
        with get_db_session() as session:
            app = session.scalar(select(Application).where(Application.id == app_id))
            if app:
                app.status = status
                session.flush()
                return app
            return None

    # --- Knowledge Vault ---

    @staticmethod
    def add_knowledge_item(title: str, category: str, content: str,
                           tags: Optional[str] = None) -> KnowledgeItem:
        with get_db_session() as session:
            item = KnowledgeItem(
                title=title, category=category, content=content, tags=tags
            )
            session.add(item)
            session.flush()
            return item

    @staticmethod
    def get_knowledge_items(category: Optional[str] = None) -> List[KnowledgeItem]:
        with get_db_session() as session:
            stmt = select(KnowledgeItem)
            if category:
                stmt = stmt.where(KnowledgeItem.category == category)
            stmt = stmt.order_by(KnowledgeItem.created_at.desc())
            return list(session.scalars(stmt).all())

    @staticmethod
    def search_knowledge(query: str) -> List[KnowledgeItem]:
        """Simple substring search across title, content, and tags."""
        with get_db_session() as session:
            stmt = select(KnowledgeItem).where(
                KnowledgeItem.title.ilike(f"%{query}%") |
                KnowledgeItem.content.ilike(f"%{query}%") |
                KnowledgeItem.tags.ilike(f"%{query}%")
            )
            return list(session.scalars(stmt).all())

    @staticmethod
    def delete_knowledge_item(item_id: int) -> bool:
        with get_db_session() as session:
            item = session.scalar(select(KnowledgeItem).where(KnowledgeItem.id == item_id))
            if item:
                session.delete(item)
                return True
            return False

    # --- Projects ---

    @staticmethod
    def add_project(name: str, description: Optional[str] = None,
                    progress: int = 0) -> Project:
        with get_db_session() as session:
            existing = session.scalar(select(Project).where(Project.name == name))
            if existing:
                return existing
            project = Project(name=name, description=description, progress=progress)
            session.add(project)
            session.flush()
            return project

    @staticmethod
    def get_projects(status: Optional[str] = None) -> List[Project]:
        with get_db_session() as session:
            stmt = select(Project)
            if status:
                stmt = stmt.where(Project.status == status)
            stmt = stmt.order_by(Project.name.asc())
            return list(session.scalars(stmt).all())

    @staticmethod
    def update_project(project_id: int, progress: Optional[int] = None,
                       status: Optional[str] = None) -> Optional[Project]:
        with get_db_session() as session:
            project = session.scalar(select(Project).where(Project.id == project_id))
            if project:
                if progress is not None:
                    project.progress = min(max(progress, 0), 100)
                if status is not None:
                    project.status = status
                session.flush()
                return project
            return None
