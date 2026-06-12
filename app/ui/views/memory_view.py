from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, 
    QLineEdit, QFrame, QComboBox
)
from PySide6.QtCore import Qt
from app.memory.memory_manager import MemoryManager

class MemoryView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 1. Header
        header = QLabel("Memory Bank & Profile Preferences", self)
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #818cf8;")
        layout.addWidget(header)
        
        desc = QLabel("These are the items and preferences FRIDAY has remembered about you. You can add items manually or delete them to modify her memory context.", self)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #9ca3af; font-size: 13px; margin-bottom: 5px;")
        layout.addWidget(desc)

        # 2. Add Memory Card (Input Panel)
        add_card = QFrame(self)
        add_card.setObjectName("Card")
        add_layout = QHBoxLayout(add_card)
        add_layout.setContentsMargins(10, 10, 10, 10)
        add_layout.setSpacing(10)

        self.cat_select = QComboBox(add_card)
        self.cat_select.addItems(["user_info", "user_preferences", "project_details", "learned_facts"])
        self.cat_select.setMinimumWidth(150)
        
        self.mem_input = QLineEdit(add_card)
        self.mem_input.setPlaceholderText("Enter preference or fact...")
        
        save_btn = QPushButton("Save Memory", add_card)
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self.save_manual_memory)

        add_layout.addWidget(self.cat_select)
        add_layout.addWidget(self.mem_input)
        add_layout.addWidget(save_btn)
        
        layout.addWidget(add_card)

        # 3. Memory Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Category", "Fact / Preference", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        
        layout.addWidget(self.table)

        # Load data
        self.refresh()

    def refresh(self):
        """Fetches memory items from database and builds table rows."""
        self.table.clearContents()
        self.table.setRowCount(0)
        
        memories = MemoryManager.get_memory_items()
        self.table.setRowCount(len(memories))

        for row, mem in enumerate(memories):
            cat_item = QTableWidgetItem(mem.category)
            content_item = QTableWidgetItem(mem.content)
            
            # Alignments
            cat_item.setTextAlignment(Qt.AlignCenter)
            
            # Create "Forget" action button
            forget_btn = QPushButton("Forget", self.table)
            forget_btn.setObjectName("DangerBtn")
            forget_btn.setMinimumHeight(24)
            forget_btn.setStyleSheet("padding: 2px 10px; font-size:11px;")
            # Bind the memory ID using property
            forget_btn.setProperty("mem_id", mem.id)
            forget_btn.clicked.connect(self.forget_memory_item)

            self.table.setItem(row, 0, cat_item)
            self.table.setItem(row, 1, content_item)
            # Embed button in table cell
            self.table.setCellWidget(row, 2, forget_btn)

    def save_manual_memory(self):
        content = self.mem_input.text().strip()
        if content:
            category = self.cat_select.currentText()
            MemoryManager.add_memory_item(category=category, content=content)
            self.mem_input.clear()
            self.refresh()

    def forget_memory_item(self):
        btn = self.sender()
        if btn:
            mem_id = btn.property("mem_id")
            if mem_id:
                MemoryManager.delete_memory_item(mem_id)
                self.refresh()
