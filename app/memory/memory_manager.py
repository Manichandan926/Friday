import re
from datetime import datetime
from typing import List, Optional
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError, DatabaseError
from app.memory.database import get_db_session
from app.memory.models import Conversation, Message, MemoryItem, Email, Task, Application, KnowledgeItem, Project, Notification, Plan, PlanStep, Routine, UsageRecord

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
    def get_conversation(conversation_id: int) -> Optional[Conversation]:
        with get_db_session() as session:
            return session.scalar(select(Conversation).where(Conversation.id == conversation_id))

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

    @staticmethod
    def get_recent_messages(conversation_id: int, limit: int) -> List[Message]:
        """The last `limit` messages of a conversation, in chronological order.

        Bounded by SQL (ORDER BY id DESC LIMIT n) so callers that only need the
        tail don't load the whole conversation into RAM — at scale that is the
        difference between reading 4 rows and reading a million.
        """
        with get_db_session() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())
                .limit(limit)
            )
            return list(reversed(session.scalars(stmt).all()))

    @staticmethod
    def get_messages_after(conversation_id: int, after_id: int) -> List[Message]:
        """Messages not yet covered by the rolling summary (id > after_id)."""
        with get_db_session() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.id > after_id)
                .order_by(Message.id.asc())
            )
            return list(session.scalars(stmt).all())

    @staticmethod
    def set_summary(conversation_id: int, summary: str, until_id: int) -> None:
        """Store the rolling summary and the last message id it covers."""
        with get_db_session() as session:
            conv = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
            if conv:
                conv.summary = summary
                conv.summary_until_id = until_id
                session.flush()

    # --- Memory (Facts/Preferences) ---

    @staticmethod
    def add_memory_item(category: str, content: str) -> MemoryItem:
        with get_db_session() as session:
            item = MemoryItem(category=category, content=content)
            session.add(item)
            session.flush()
            return item

    @staticmethod
    def get_memory_items(category: Optional[str] = None,
                         limit: Optional[int] = None) -> List[MemoryItem]:
        """Long-term facts, newest first. `limit` bounds the candidate set for
        hot-path callers (recall, dedup) so per-turn cost stays flat as the
        store grows — without a limit this scans every fact ever saved."""
        with get_db_session() as session:
            stmt = select(MemoryItem)
            if category:
                stmt = stmt.where(MemoryItem.category == category)
            stmt = stmt.order_by(MemoryItem.created_at.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
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
        """Full-text search across title, content, and tags, BM25-ranked
        (most relevant first) via the SQLite FTS5 index. Terms are prefix-
        matched and OR-combined for recall (a search for 'sql graph' finds
        notes mentioning either). Falls back to a LIKE substring search when
        FTS5 is unavailable or the query has no indexable terms."""
        q = (query or "").strip()
        if not q:
            return []
        with get_db_session() as session:
            terms = re.findall(r"\w+", q)
            if terms:
                # Quote each term (so punctuation can't inject FTS operators) and
                # prefix-match it; OR them together.
                match_expr = " OR ".join(f'"{t}"*' for t in terms)
                try:
                    rows = session.execute(
                        text("SELECT ki.id FROM knowledge_items ki "
                             "JOIN knowledge_fts f ON f.rowid = ki.id "
                             "WHERE knowledge_fts MATCH :expr "
                             "ORDER BY bm25(knowledge_fts)"),
                        {"expr": match_expr},
                    ).all()
                    ids = [r[0] for r in rows]
                    if not ids:
                        return []
                    by_id = {it.id: it for it in session.scalars(
                        select(KnowledgeItem).where(KnowledgeItem.id.in_(ids)))}
                    return [by_id[i] for i in ids if i in by_id]  # preserve rank
                except (OperationalError, DatabaseError):
                    pass  # no FTS index / FTS5 unavailable → LIKE fallback below
            stmt = select(KnowledgeItem).where(
                KnowledgeItem.title.ilike(f"%{q}%") |
                KnowledgeItem.content.ilike(f"%{q}%") |
                KnowledgeItem.tags.ilike(f"%{q}%")
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

    # --- Notifications ---

    @staticmethod
    def add_notification(title: str, message: str, category: str = "general") -> Notification:
        with get_db_session() as session:
            notif = Notification(title=title, message=message, category=category)
            session.add(notif)
            session.flush()
            return notif

    @staticmethod
    def get_notifications(unread_only: bool = False, limit: int = 50) -> List[Notification]:
        with get_db_session() as session:
            stmt = select(Notification)
            if unread_only:
                stmt = stmt.where(Notification.is_read == False)
            stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
            return list(session.scalars(stmt).all())

    # --- LLM usage / cost ---

    @staticmethod
    def add_usage_record(provider: str, model: str, input_tokens: int,
                         output_tokens: int, cost: float, cost_known: bool) -> UsageRecord:
        with get_db_session() as session:
            rec = UsageRecord(
                provider=provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost=cost, cost_known=cost_known,
            )
            session.add(rec)
            session.flush()
            return rec

    @staticmethod
    def get_usage_totals() -> List[dict]:
        """All-time usage aggregated per provider/model (newest schema)."""
        with get_db_session() as session:
            stmt = (
                select(
                    UsageRecord.provider,
                    UsageRecord.model,
                    func.count(UsageRecord.id),
                    func.sum(UsageRecord.input_tokens),
                    func.sum(UsageRecord.output_tokens),
                    func.sum(UsageRecord.cost),
                    func.min(UsageRecord.cost_known),  # 0 if any call was unpriced
                )
                .group_by(UsageRecord.provider, UsageRecord.model)
            )
            totals = []
            for provider, model, calls, tin, tout, tcost, known in session.execute(stmt).all():
                totals.append({
                    "provider": provider,
                    "model": model,
                    "calls": calls,
                    "input_tokens": int(tin or 0),
                    "output_tokens": int(tout or 0),
                    "cost": float(tcost or 0.0),
                    "cost_known": bool(known),
                })
            return totals

    # --- Analytics aggregates (computed in SQL, not by loading rows) ---

    @staticmethod
    def count_tasks(status: Optional[str] = None,
                    created_after: Optional[datetime] = None,
                    created_before: Optional[datetime] = None,
                    due_before: Optional[datetime] = None) -> int:
        """Bounded COUNT over tasks. `due_before` implies a real due_date
        (NULLs excluded), so status='pending' + due_before=now = overdue."""
        with get_db_session() as session:
            stmt = select(func.count(Task.id))
            if status is not None:
                stmt = stmt.where(Task.status == status)
            if created_after is not None:
                stmt = stmt.where(Task.created_at >= created_after)
            if created_before is not None:
                stmt = stmt.where(Task.created_at < created_before)
            if due_before is not None:
                stmt = stmt.where(Task.due_date.is_not(None), Task.due_date < due_before)
            return int(session.scalar(stmt) or 0)

    @staticmethod
    def sum_usage_tokens(created_after: Optional[datetime] = None,
                         provider: Optional[str] = None) -> tuple:
        """(input_tokens, output_tokens, calls) summed in SQL. `provider` is
        matched case-insensitively (records store 'Groq', 'OpenAI', …)."""
        with get_db_session() as session:
            stmt = select(
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.count(UsageRecord.id),
            )
            if created_after is not None:
                stmt = stmt.where(UsageRecord.created_at >= created_after)
            if provider is not None:
                stmt = stmt.where(func.lower(UsageRecord.provider) == provider.lower())
            tin, tout, calls = session.execute(stmt).one()
            return int(tin), int(tout), int(calls)

    # --- Routines (standing autonomous behaviors) ---

    @staticmethod
    def add_routine(name: str, instruction: str, schedule_type: str,
                    time_of_day: Optional[str] = None,
                    interval_minutes: Optional[int] = None) -> Routine:
        with get_db_session() as session:
            routine = Routine(
                name=name, instruction=instruction, schedule_type=schedule_type,
                time_of_day=time_of_day, interval_minutes=interval_minutes,
            )
            session.add(routine)
            session.flush()
            return routine

    @staticmethod
    def get_routine(routine_id: int) -> Optional[Routine]:
        with get_db_session() as session:
            return session.get(Routine, routine_id)

    @staticmethod
    def get_routine_by_name(name: str) -> Optional[Routine]:
        with get_db_session() as session:
            return session.scalars(
                select(Routine).where(func.lower(Routine.name) == name.lower())
            ).first()

    @staticmethod
    def get_routines(enabled_only: bool = False) -> List[Routine]:
        with get_db_session() as session:
            stmt = select(Routine).order_by(Routine.id)
            if enabled_only:
                stmt = stmt.where(Routine.enabled == True)  # noqa: E712
            return list(session.scalars(stmt).all())

    @staticmethod
    def set_routine_enabled(routine_id: int, enabled: bool) -> Optional[Routine]:
        with get_db_session() as session:
            routine = session.get(Routine, routine_id)
            if routine is None:
                return None
            routine.enabled = enabled
            session.flush()
            return routine

    @staticmethod
    def delete_routine(routine_id: int) -> bool:
        with get_db_session() as session:
            routine = session.get(Routine, routine_id)
            if routine is None:
                return False
            session.delete(routine)
            return True

    @staticmethod
    def touch_routine(routine_id: int, ran_at: datetime) -> None:
        with get_db_session() as session:
            routine = session.get(Routine, routine_id)
            if routine is not None:
                routine.last_run_at = ran_at
                session.flush()

    # --- Task-mode plans ---

    @staticmethod
    def create_plan(goal: str, steps: List[str],
                    conversation_id: Optional[int] = None) -> Plan:
        with get_db_session() as session:
            plan = Plan(goal=goal, conversation_id=conversation_id)
            session.add(plan)
            session.flush()
            for i, desc in enumerate(steps, 1):
                session.add(PlanStep(plan_id=plan.id, seq=i, description=desc))
            session.flush()
            return plan

    @staticmethod
    def get_plan(plan_id: int) -> Optional[Plan]:
        with get_db_session() as session:
            return session.get(Plan, plan_id)

    @staticmethod
    def get_open_plan() -> Optional[Plan]:
        """The single in-flight plan (active, or blocked on a failed step)."""
        with get_db_session() as session:
            return session.scalars(
                select(Plan).where(Plan.status.in_(("active", "blocked")))
                .order_by(Plan.id.desc())
            ).first()

    @staticmethod
    def get_latest_plan() -> Optional[Plan]:
        with get_db_session() as session:
            return session.scalars(select(Plan).order_by(Plan.id.desc())).first()

    @staticmethod
    def get_plan_steps(plan_id: int) -> List[PlanStep]:
        with get_db_session() as session:
            return list(session.scalars(
                select(PlanStep).where(PlanStep.plan_id == plan_id)
                .order_by(PlanStep.seq)
            ).all())

    @staticmethod
    def get_plan_step(step_id: int) -> Optional[PlanStep]:
        with get_db_session() as session:
            return session.get(PlanStep, step_id)

    @staticmethod
    def update_plan_step(step_id: int, status: str,
                         result: Optional[str] = None) -> Optional[PlanStep]:
        with get_db_session() as session:
            step = session.get(PlanStep, step_id)
            if step is None:
                return None
            step.status = status
            if result is not None:
                step.result = result
            session.flush()
            return step

    @staticmethod
    def set_plan_status(plan_id: int, status: str,
                        result: Optional[str] = None) -> Optional[Plan]:
        with get_db_session() as session:
            plan = session.get(Plan, plan_id)
            if plan is None:
                return None
            plan.status = status
            if result is not None:
                plan.result = result
            session.flush()
            return plan

    # --- Notifications ---

    @staticmethod
    def mark_notifications_read(ids: Optional[List[int]] = None) -> int:
        """Mark notifications read. With `ids`, only those (so a nudge that
        arrives mid-turn isn't marked read before it's ever surfaced);
        without, all unread."""
        with get_db_session() as session:
            stmt = select(Notification).where(Notification.is_read == False)
            if ids is not None:
                stmt = stmt.where(Notification.id.in_(ids))
            unread = session.scalars(stmt).all()
            count = 0
            for n in unread:
                n.is_read = True
                count += 1
            session.flush()
            return count
