from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser, QFrame
)
from PySide6.QtCore import Qt
from app.memory.memory_manager import MemoryManager

class EmailsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 1. Header
        header = QLabel("Email Priorities & Action Extractions", self)
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #818cf8;")
        layout.addWidget(header)

        # 2. Main Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Received At", "Sender", "Subject", "Priority"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self.display_email_details)
        layout.addWidget(self.table)

        # 3. Bottom Card: Details View
        self.detail_card = QFrame(self)
        self.detail_card.setObjectName("Card")
        self.detail_card.setMinimumHeight(200)
        
        detail_layout = QVBoxLayout(self.detail_card)
        detail_layout.setContentsMargins(15, 15, 15, 15)
        detail_layout.setSpacing(10)

        self.detail_title = QLabel("Select an email to view summary and action items", self.detail_card)
        self.detail_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #818cf8;")
        detail_layout.addWidget(self.detail_title)

        self.detail_browser = QTextBrowser(self.detail_card)
        self.detail_browser.setStyleSheet("background-color: transparent; border: none;")
        detail_layout.addWidget(self.detail_browser)

        layout.addWidget(self.detail_card)

        # Load data
        self.refresh()

    def refresh(self):
        """Reloads email lists from the local SQLite database."""
        self.table.clearContents()
        self.table.setRowCount(0)
        
        emails = MemoryManager.get_emails()
        self.table.setRowCount(len(emails))

        # Pre-populate demo emails if none exist for a stunning first impression
        if not emails:
            # Let's seed a couple of premium sample emails
            import datetime
            now = datetime.datetime.now()
            
            MemoryManager.save_email(
                email_id="demo_msg_1",
                subject="Action Required: Review Project FRIDAY Architecture",
                sender="supervisor@company.com",
                body_summary="The supervisor requested a review of the FRIDAY background service architectures. Focus on optimizing memory footprint and SQLite interaction logs.",
                received_at=now - datetime.timedelta(hours=2),
                priority="high",
                action_items='["Review database performance benchmarks", "Verify Groq API key rotation mechanism"]'
            )
            
            MemoryManager.save_email(
                email_id="demo_msg_2",
                subject="Weekly Engineering Portfolio Progress",
                sender="portfolio-alerts@github.com",
                body_summary="Your personal portfolio showcasing Spring Boot and AI features received 3 new star events. The CSS styling has been aligned to modern Void themes.",
                received_at=now - datetime.timedelta(hours=5),
                priority="low",
                action_items='["Push latest data.js configuration update", "Verify responsive CSS layout breakpoints"]'
            )
            emails = MemoryManager.get_emails()
            self.table.setRowCount(len(emails))

        for row, email in enumerate(emails):
            # Formatted items
            date_str = email.received_at.strftime("%Y-%m-%d %H:%M")
            date_item = QTableWidgetItem(date_str)
            sender_item = QTableWidgetItem(email.sender)
            subject_item = QTableWidgetItem(email.subject)
            priority_item = QTableWidgetItem(email.priority.upper())

            # Text alignment
            date_item.setTextAlignment(Qt.AlignCenter)
            priority_item.setTextAlignment(Qt.AlignCenter)

            # Highlight high priorities
            if email.priority.lower() == "high":
                priority_item.setForeground(Qt.red)
            elif email.priority.lower() == "medium":
                priority_item.setForeground(Qt.yellow)
            else:
                priority_item.setForeground(Qt.green)

            # Store full email object reference
            date_item.setData(Qt.UserRole, email)

            self.table.setItem(row, 0, date_item)
            self.table.setItem(row, 1, sender_item)
            self.table.setItem(row, 2, subject_item)
            self.table.setItem(row, 3, priority_item)

    def display_email_details(self):
        selected_ranges = self.table.selectedRanges()
        if not selected_ranges:
            return

        row = selected_ranges[0].topRow()
        date_item = self.table.item(row, 0)
        if not date_item:
            return

        email = date_item.data(Qt.UserRole)
        self.detail_title.setText(f"Subject: {email.subject}")

        # Parse actions
        actions_html = ""
        if email.action_items:
            try:
                import json
                items = json.loads(email.action_items)
                if isinstance(items, list) and items:
                    actions_html = "<h4 style='color: #ef4444; margin-top:10px;'>Extracted Action Items:</h4><ul>"
                    for it in items:
                        actions_html += f"<li>{it}</li>"
                    actions_html += "</ul>"
            except Exception:
                actions_html = f"<h4 style='color: #ef4444;'>Extracted Action Items:</h4><p>{email.action_items}</p>"

        body_html = f"""
        <p><b>From:</b> {email.sender}<br/>
        <b>Received:</b> {email.received_at.strftime('%Y-%m-%d %H:%M:%S')}<br/>
        <b>Priority:</b> <span style="text-transform:uppercase; font-weight:bold; color:{'#ef4444' if email.priority == 'high' else '#f59e0b' if email.priority == 'medium' else '#10b981'}">{email.priority}</span></p>
        <hr style="border-color: #1f2937;"/>
        <h4 style="color: #818cf8;">Summary:</h4>
        <p style="line-height: 1.5; color: #d1d5db;">{email.body_summary}</p>
        {actions_html}
        """
        self.detail_browser.setHtml(body_html)
