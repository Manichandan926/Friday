import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, 
    QLineEdit, QFrame, QComboBox, QDateTimeEdit
)
from PySide6.QtCore import Qt, QDateTime
from app.memory.memory_manager import MemoryManager

class TasksView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 1. Header
        header = QLabel("Active Tasks & Reminders", self)
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #818cf8;")
        layout.addWidget(header)

        # 2. Add Task Form (Card)
        form_card = QFrame(self)
        form_card.setObjectName("Card")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(15, 15, 15, 15)
        form_layout.setSpacing(10)

        form_title = QLabel("Create New Task", form_card)
        form_title.setStyleSheet("font-weight: bold; color: #10b981;")
        form_layout.addWidget(form_title)

        inputs_layout = QHBoxLayout()
        self.title_input = QLineEdit(form_card)
        self.title_input.setPlaceholderText("Task Title")
        
        self.desc_input = QLineEdit(form_card)
        self.desc_input.setPlaceholderText("Description (Optional)")

        self.priority_select = QComboBox(form_card)
        self.priority_select.addItems(["low", "medium", "high"])
        self.priority_select.setCurrentText("medium")

        self.due_select = QDateTimeEdit(form_card)
        self.due_select.setCalendarPopup(True)
        # Default due date to tomorrow
        self.due_select.setDateTime(QDateTime.currentDateTime().addDays(1))

        add_btn = QPushButton("Add Task", form_card)
        add_btn.setObjectName("PrimaryBtn")
        add_btn.clicked.connect(self.create_task)

        inputs_layout.addWidget(self.title_input)
        inputs_layout.addWidget(self.desc_input)
        inputs_layout.addWidget(self.priority_select)
        inputs_layout.addWidget(self.due_select)
        inputs_layout.addWidget(add_btn)
        form_layout.addLayout(inputs_layout)

        layout.addWidget(form_card)

        # 3. Tasks Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Title", "Description", "Priority", "Due Date", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        
        layout.addWidget(self.table)

        # Initial loading
        self.refresh()

    def refresh(self):
        """Fetches pending and completed tasks from database and redraws list."""
        self.table.clearContents()
        self.table.setRowCount(0)
        
        # Load all tasks
        tasks = MemoryManager.get_tasks()
        self.table.setRowCount(len(tasks))

        for row, task in enumerate(tasks):
            title_text = task.title
            if task.status == "completed":
                title_text = f"✓ {title_text}"
            
            title_item = QTableWidgetItem(title_text)
            desc_item = QTableWidgetItem(task.description or "")
            priority_item = QTableWidgetItem(task.priority.upper())
            
            due_text = task.due_date.strftime("%Y-%m-%d %H:%M") if task.due_date else "No Limit"
            due_item = QTableWidgetItem(due_text)

            # Alignments
            priority_item.setTextAlignment(Qt.AlignCenter)
            due_item.setTextAlignment(Qt.AlignCenter)

            # Styling completed tasks/priorities
            if task.status == "completed":
                title_item.setForeground(Qt.gray)
                desc_item.setForeground(Qt.gray)
                priority_item.setForeground(Qt.gray)
                due_item.setForeground(Qt.gray)
            else:
                if task.priority.lower() == "high":
                    priority_item.setForeground(Qt.red)
                elif task.priority.lower() == "medium":
                    priority_item.setForeground(Qt.yellow)
                else:
                    priority_item.setForeground(Qt.green)

            # Action button: "Complete" or "Re-open"
            action_btn = QPushButton(self.table)
            if task.status == "completed":
                action_btn.setText("Re-open")
                action_btn.setStyleSheet("padding: 2px 10px; font-size:11px; background-color:#374151;")
            else:
                action_btn.setText("Complete")
                action_btn.setObjectName("PrimaryBtn")
                action_btn.setStyleSheet("padding: 2px 10px; font-size:11px;")
                
            action_btn.setProperty("task_id", task.id)
            action_btn.setProperty("current_status", task.status)
            action_btn.clicked.connect(self.toggle_task_status)

            self.table.setItem(row, 0, title_item)
            self.table.setItem(row, 1, desc_item)
            self.table.setItem(row, 2, priority_item)
            self.table.setItem(row, 3, due_item)
            self.table.setCellWidget(row, 4, action_btn)

    def create_task(self):
        title = self.title_input.text().strip()
        if not title:
            return

        desc = self.desc_input.text().strip() or None
        priority = self.priority_select.currentText()
        
        # Convert QDateTime to python datetime
        qdt = self.due_select.dateTime()
        due_date = datetime.datetime.fromisoformat(qdt.toString(Qt.ISODate))

        MemoryManager.add_task(
            title=title,
            description=desc,
            priority=priority,
            due_date=due_date
        )

        self.title_input.clear()
        self.desc_input.clear()
        self.refresh()

    def toggle_task_status(self):
        btn = self.sender()
        if btn:
            task_id = btn.property("task_id")
            current_status = btn.property("current_status")
            new_status = "completed" if current_status == "pending" else "pending"
            
            MemoryManager.update_task_status(task_id, new_status)
            self.refresh()
