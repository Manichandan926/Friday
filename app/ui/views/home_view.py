import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QFrame, QListWidget, QPushButton, QLineEdit
)
from PySide6.QtCore import Qt, QTimer
from app.memory.memory_manager import MemoryManager

class HomeView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
        
        # system stats refresh (3s)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_system_stats)
        self.stats_timer.start(3000)

        # dashboard data refresh (30s) — emails, tasks, applications
        self.data_timer = QTimer(self)
        self.data_timer.timeout.connect(self.refresh)
        self.data_timer.start(30000)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # 1. Welcome Header
        self.welcome_label = QLabel("Hello, User", self)
        self.welcome_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #818cf8;")
        layout.addWidget(self.welcome_label)

        # 2. KPI Cards Layout
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(15)

        self.email_card = self.create_kpi_card("Emails Summarized", "0")
        self.task_card = self.create_kpi_card("Pending Tasks", "0")
        self.app_card = self.create_kpi_card("Applications Tracked", "0")
        self.memory_card = self.create_kpi_card("Memory Facts", "0")

        kpi_layout.addWidget(self.email_card)
        kpi_layout.addWidget(self.task_card)
        kpi_layout.addWidget(self.app_card)
        kpi_layout.addWidget(self.memory_card)
        layout.addLayout(kpi_layout)

        # 3. System Stats & Quick Action Layout
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(15)

        self.stats_card = QFrame(self)
        self.stats_card.setObjectName("Card")
        stats_card_layout = QVBoxLayout(self.stats_card)
        stats_card_layout.setContentsMargins(15, 15, 15, 15)

        stats_title = QLabel("System Status", self.stats_card)
        stats_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #818cf8;")
        stats_card_layout.addWidget(stats_title)

        self.cpu_label = QLabel("CPU Usage: 0.0%", self.stats_card)
        self.ram_label = QLabel("RAM Usage: 0.0%", self.stats_card)
        stats_card_layout.addWidget(self.cpu_label)
        stats_card_layout.addWidget(self.ram_label)
        stats_layout.addWidget(self.stats_card)

        # 4. Quick Action Card (Add Task)
        quick_card = QFrame(self)
        quick_card.setObjectName("Card")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(15, 15, 15, 15)

        quick_title = QLabel("Quick Actions", quick_card)
        quick_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #10b981;")
        quick_layout.addWidget(quick_title)

        input_layout = QHBoxLayout()
        self.task_input = QLineEdit(self)
        self.task_input.setPlaceholderText("Enter quick task description...")
        
        self.add_btn = QPushButton("Add Task", self)
        self.add_btn.setObjectName("PrimaryBtn")
        self.add_btn.clicked.connect(self.add_quick_task)
        
        input_layout.addWidget(self.task_input)
        input_layout.addWidget(self.add_btn)
        quick_layout.addLayout(input_layout)
        stats_layout.addWidget(quick_card)

        layout.addLayout(stats_layout)

        # 5. Daily Brief / Activity Log Feed
        activity_title = QLabel("FRIDAY Daily Briefing & Overview", self)
        activity_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #9ca3af;")
        layout.addWidget(activity_title)

        self.activity_list = QListWidget(self)
        self.activity_list.setStyleSheet("""
            QListWidget {
                background-color: #1e1e2e;
                border: 1px solid #313244;
                border-radius: 8px;
                padding: 10px;
                font-family: 'Courier New', monospace;
                color: #cdd6f4;
            }
        """)
        layout.addWidget(self.activity_list)

        # Initial Refresh
        self.refresh()

    def create_kpi_card(self, title: str, val: str) -> QFrame:
        card = QFrame(self)
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 15, 15, 15)

        val_label = QLabel(val, card)
        val_label.setObjectName("kpi_value")
        val_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #f3f4f6;")
        
        title_label = QLabel(title, card)
        title_label.setStyleSheet("color: #9ca3af; font-size: 12px;")

        card_layout.addWidget(val_label)
        card_layout.addWidget(title_label)
        
        # Attach reference to the value label so we can update it
        card.setProperty("value_label", val_label)
        return card

    def refresh(self):
        """Fetches dynamic counts from SQLite and reloads user info."""
        # Retrieve user name from memories
        user_name = "Mani Chandan"
        memories = MemoryManager.get_memory_items()
        for item in memories:
            if "name is" in item.content.lower():
                parts = item.content.split("is")
                if len(parts) > 1:
                    user_name = parts[-1].strip().replace(".", "")
                    break

        self.welcome_label.setText(f"Welcome Back, {user_name}")

        # Update KPI Cards
        emails = MemoryManager.get_emails()
        pending_tasks = MemoryManager.get_tasks(status="pending")
        apps = MemoryManager.get_applications()
        
        self.email_card.property("value_label").setText(str(len(emails)))
        self.task_card.property("value_label").setText(str(len(pending_tasks)))
        self.app_card.property("value_label").setText(str(len(apps)))
        self.memory_card.property("value_label").setText(str(len(memories)))

        # Update Daily Brief
        self.activity_list.clear()
        
        # 1. Overview header
        self.activity_list.addItem("================== FRIDAY DAILY BRIEF ==================")
        
        important_emails_count = len([e for e in emails if e.priority in ['high', 'medium']])
        self.activity_list.addItem(f"• Important Unread Emails: {important_emails_count}")
        self.activity_list.addItem(f"• Total Pending Tasks:      {len(pending_tasks)}")
        self.activity_list.addItem(f"• Tracked Applications:     {len(apps)}")
        
        # 2. Top priority focus item
        high_tasks = [t for t in pending_tasks if t.priority == "high"]
        top_focus = high_tasks[0].title if high_tasks else (pending_tasks[0].title if pending_tasks else "None")
        self.activity_list.addItem(f"• Top Priority Focus:       {top_focus}")
        
        # 3. Active Applications List
        if apps:
            self.activity_list.addItem("")
            self.activity_list.addItem("---------------- Placement Pipeline Status ----------------")
            for app in apps[:3]:
                deadline_str = f" (Deadline: {app.deadline.strftime('%b %d')})" if app.deadline else ""
                self.activity_list.addItem(f"  - {app.company} | {app.role} -> {app.status.upper()}{deadline_str}")
        
        # 4. Recent emails list
        if emails:
            self.activity_list.addItem("")
            self.activity_list.addItem("---------------- Recent Emails ----------------")
            for e in emails[:3]:
                self.activity_list.addItem(f"  - [{e.priority.upper()}] {e.subject} (From: {e.sender.split('<')[0].strip()})")

        # Update stats
        self.update_system_stats()

    def update_system_stats(self):
        """Read current CPU and RAM using native Linux procfs to avoid external dependencies."""
        cpu_str = "N/A"
        ram_str = "N/A"
        
        # 1. RAM Usage
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            total = 0
            avail = 0
            for line in lines:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
            if total > 0:
                used = total - avail
                ram_percent = round((used / total) * 100, 1)
                ram_str = f"{ram_percent}%"
        except Exception:
            pass

        # 2. CPU Usage (using /proc/loadavg / number of CPU cores)
        try:
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()[0]
            cores = os.cpu_count() or 4
            cpu_percent = min(round((float(load) / cores) * 100, 1), 100.0)
            cpu_str = f"{cpu_percent}%"
        except Exception:
            pass

        self.cpu_label.setText(f"CPU Usage: {cpu_str}")
        self.ram_label.setText(f"RAM Usage: {ram_str}")

    def add_quick_task(self):
        task_text = self.task_input.text().strip()
        if task_text:
            MemoryManager.add_task(title=task_text)
            self.task_input.clear()
            self.refresh()
