import datetime
import subprocess
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.logger import logger
from app.memory.memory_manager import MemoryManager

class FridayScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        # Keep track of reminded tasks to avoid repeating notifications
        self._notified_tasks = set()

    def start(self) -> None:
        logger.info("Initializing FRIDAY background scheduler...")
        
        # Check emails every 5 minutes
        self.scheduler.add_job(
            self._check_emails_job, 
            "interval", 
            minutes=5, 
            id="check_emails",
            replace_existing=True
        )
        
        # Check tasks/reminders every 1 minute
        self.scheduler.add_job(
            self._check_reminders_job, 
            "interval", 
            minutes=1, 
            id="check_reminders",
            replace_existing=True
        )

        # Database backup every 24 hours
        self.scheduler.add_job(
            self._backup_database_job,
            "interval",
            hours=24,
            id="db_backup",
            replace_existing=True
        )

        # Proactive deadline monitor every 30 minutes
        self.scheduler.add_job(
            self._proactive_deadline_check,
            "interval",
            minutes=30,
            id="deadline_monitor",
            replace_existing=True
        )

        # System health monitor every 10 minutes
        self.scheduler.add_job(
            self._system_health_check,
            "interval",
            minutes=10,
            id="system_health",
            replace_existing=True
        )

        # User-defined autonomous routines — checked every minute; the runner
        # itself decides what's due and consumes runs (see routine_runner.py)
        self.scheduler.add_job(
            self._routines_job,
            "interval",
            minutes=1,
            id="routines",
            replace_existing=True
        )
        
        self.scheduler.start()
        logger.info("FRIDAY background scheduler started.")

    def shutdown(self) -> None:
        logger.info("Stopping FRIDAY background scheduler...")
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("FRIDAY background scheduler stopped.")

    def _check_emails_job(self) -> None:
        """Scheduled job to check, summarize, and prioritize emails."""
        logger.info("Executing scheduled email check...")
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._process_emails_workflow())
        except Exception as e:
            logger.error(f"Error in emails background sync: {e}")
        finally:
            loop.close()

    async def _process_emails_workflow(self) -> None:
        from app.email.gmail import get_gmail_service, fetch_unread_emails, get_email_details
        from app.email.analyzer import EmailAnalyzer
        import json
        import email.utils

        # Initialize Gmail Client
        service = get_gmail_service()
        if not service:
            logger.debug("Skipping Gmail check: service is offline (no credentials/token).")
            return

        # Fetch up to 5 unread inbox emails
        unread_list = fetch_unread_emails(service, max_results=5)
        if not unread_list:
            logger.debug("No unread emails in Gmail inbox.")
            return

        analyzer = EmailAnalyzer()
        # Fetch already processed email IDs from database
        processed_emails = {e.id for e in MemoryManager.get_emails()}

        for item in unread_list:
            msg_id = item['id']
            if msg_id in processed_emails:
                continue

            # Fetch details
            details = get_email_details(service, msg_id)
            if not details:
                continue

            # Analyze email content
            logger.info(f"Analyzing incoming email: '{details['subject']}' from {details['sender']}")
            analysis = await analyzer.analyze(details['sender'], details['subject'], details['body'])
            if not analysis:
                continue

            # Parse results
            priority = analysis.get("priority", "medium").lower()
            category = analysis.get("category", "newsletter").lower()
            summary = analysis.get("summary", "No summary.")
            action_items = analysis.get("action_items", [])
            deadlines = analysis.get("deadlines", [])

            # Parse received date
            received_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            if details.get("received_at_str"):
                try:
                    parsed_dt = email.utils.parsedate_to_datetime(details["received_at_str"])
                    received_at = parsed_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass

            # Save analyzed record to SQLite
            logger.info(f"Saving email record to database. Priority: {priority.upper()}")
            MemoryManager.save_email(
                email_id=msg_id,
                subject=details['subject'],
                sender=details['sender'],
                body_summary=summary,
                received_at=received_at,
                priority=priority,
                action_items=json.dumps(action_items)
            )

            # Auto-extract and track applications for placement-related categories
            if category in ["placement", "internship", "interview"]:
                try:
                    from app.agents.placement_agent import PlacementAgent
                    p_agent = PlacementAgent()
                    await p_agent.parse_and_save_application(
                        sender=details['sender'],
                        subject=details['subject'],
                        body=details['body'],
                        source_email_id=msg_id
                    )
                except Exception as ex:
                    logger.error(f"Failed to track placement application: {ex}")


            # If priority is high or medium, notify and generate tasks
            if priority in ["high", "medium"]:
                # Send Desktop notification
                actions_str = ", ".join(action_items) if action_items else "Review details"
                deadline_str = f" (Deadline: {', '.join(deadlines)})" if deadlines else ""
                
                self._send_notification(
                    title=f"New {priority.upper()} Priority Email",
                    message=f"From: {details['sender']}\nSubject: {details['subject']}\nAction: {actions_str}{deadline_str}"
                )

                # Insert tasks
                for act in action_items:
                    MemoryManager.add_task(
                        title=act,
                        description=f"Action item from email: '{details['subject']}' from {details['sender']}",
                        priority=priority,
                        due_date=None,
                        source_email_id=msg_id
                    )


    def _check_reminders_job(self) -> None:
        """Scheduled job to monitor upcoming task due dates."""
        logger.debug("Background job: Checking reminders...")
        try:
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            pending_tasks = MemoryManager.get_tasks(status="pending")
            
            for task in pending_tasks:
                if task.due_date and task.due_date <= now:
                    if task.id not in self._notified_tasks:
                        self._send_notification(
                            title=f"Task Due: {task.title}",
                            message=task.description or "This task is now due."
                        )
                        self._notified_tasks.add(task.id)
        except Exception as e:
            logger.error(f"Error in reminders background task: {e}")

    def _routines_job(self) -> None:
        """Run any due user-defined routines (unattended tool loops — the
        tier gate holds them to AUTO tools; see routine_runner.py)."""
        from app.core.routine_runner import run_due_routines
        from app.memory.memory_manager import MemoryManager as MM

        # cheap pre-check: don't spin up an event loop + provider every
        # minute when nothing is due
        from app.core.routine_runner import is_due
        if not any(is_due(r) for r in MM.get_routines(enabled_only=True)):
            return

        import asyncio
        from app.llm.provider import get_llm_provider
        try:
            provider = get_llm_provider()
        except Exception as e:
            logger.error(f"Routines job: no LLM provider available: {e}")
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ran = loop.run_until_complete(run_due_routines(provider))
            if ran:
                logger.info(f"Routines job: {ran} routine(s) processed.")
        except Exception as e:
            logger.error(f"Error in routines job: {e}")
        finally:
            loop.close()

    def _send_notification(self, title: str, message: str) -> None:
        """Sends native system notifications using notify-send (common on Linux/Fedora)."""
        try:
            logger.info(f"System Notification: {title} -> {message}")
            subprocess.run(
                ["notify-send", "-a", "FRIDAY", "-i", "preferences-system-notifications", title, message], 
                check=True
            )
        except FileNotFoundError:
            logger.warning("notify-send utility not found on this system. Cannot send OS notification.")
        except Exception as e:
            logger.error(f"Failed to execute notify-send: {e}")

    def _backup_database_job(self) -> None:
        """Scheduled backup task."""
        logger.info("Executing scheduled database backup...")
        from app.memory.backup import backup_db
        backup_db()

    def _proactive_deadline_check(self) -> None:
        """Monitor application deadlines and notify user about critical ones."""
        logger.debug("Proactive monitor: checking application deadlines...")
        try:
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            apps = MemoryManager.get_applications()

            for app in apps:
                if not app.deadline or app.status in ("rejected", "offer"):
                    continue

                delta = app.deadline - now
                days_left = delta.days
                alert_key = f"deadline_{app.id}_{days_left}"

                if alert_key in self._notified_tasks:
                    continue

                if days_left < 0:
                    title = f"⚠ DEADLINE PASSED: {app.company}"
                    msg = f"{app.role} deadline was {abs(days_left)} days ago!"
                    self._send_notification(title, msg)
                    MemoryManager.add_notification(title, msg, category="deadline")
                    self._notified_tasks.add(alert_key)
                elif days_left <= 1:
                    title = f"🔴 CRITICAL: {app.company}"
                    msg = f"{app.role} deadline is TOMORROW!"
                    self._send_notification(title, msg)
                    MemoryManager.add_notification(title, msg, category="deadline")
                    self._notified_tasks.add(alert_key)
                elif days_left <= 3:
                    title = f"🟡 URGENT: {app.company}"
                    msg = f"{app.role} deadline in {days_left} days."
                    self._send_notification(title, msg)
                    MemoryManager.add_notification(title, msg, category="deadline")
                    self._notified_tasks.add(alert_key)

        except Exception as e:
            logger.error(f"Proactive deadline check failed: {e}")

    def _system_health_check(self) -> None:
        """Monitor system resources using native C daemon. Falls back to /proc."""
        logger.debug("Proactive monitor: checking system health...")
        try:
            # prefer native C daemon (data already cached, zero /proc overhead)
            health = None
            try:
                from app.core.native_bridge import get_health_native
                health = get_health_native()
            except Exception:
                pass

            if health:
                usage_pct = health.get("ram_pct", 0)
                disk_pct = health.get("disk_pct", 0)
                bat_pct = health.get("bat_pct", None)
                bat_status = health.get("bat_status", "")
            else:
                # fallback: read /proc directly
                import os
                meminfo = {}
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split()
                        key = parts[0].rstrip(":")
                        if key in ("MemTotal", "MemAvailable"):
                            meminfo[key] = int(parts[1])

                total = meminfo.get("MemTotal", 1)
                avail = meminfo.get("MemAvailable", total)
                usage_pct = round((1 - avail / total) * 100, 1)

                st = os.statvfs("/")
                total_gb = (st.f_blocks * st.f_frsize) / (1024**3)
                free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
                disk_pct = round((1 - free_gb / total_gb) * 100, 1)

                bat_pct = None
                bat_status = ""

            # RAM alert
            if usage_pct > 90 and "ram_critical" not in self._notified_tasks:
                title = "🔴 RAM Critical"
                msg = f"RAM usage at {usage_pct}%. Close some applications."
                self._send_notification(title, msg)
                MemoryManager.add_notification(title, msg, category="system")
                self._notified_tasks.add("ram_critical")
            elif usage_pct < 80:
                self._notified_tasks.discard("ram_critical")

            # Disk alert
            if disk_pct > 90 and "disk_critical" not in self._notified_tasks:
                title = "🔴 Disk Space Critical"
                msg = f"Disk usage at {disk_pct}%."
                self._send_notification(title, msg)
                MemoryManager.add_notification(title, msg, category="system")
                self._notified_tasks.add("disk_critical")
            elif disk_pct < 85:
                self._notified_tasks.discard("disk_critical")

            # Battery alert (only from native daemon)
            if bat_pct is not None and bat_status == "Discharging":
                if bat_pct < 15 and "bat_critical" not in self._notified_tasks:
                    title = "🔴 Battery Critical"
                    msg = f"Battery at {int(bat_pct)}% and discharging. Plug in now."
                    self._send_notification(title, msg)
                    MemoryManager.add_notification(title, msg, category="system")
                    self._notified_tasks.add("bat_critical")
                elif bat_pct > 25:
                    self._notified_tasks.discard("bat_critical")

        except Exception as e:
            logger.error(f"System health check failed: {e}")

