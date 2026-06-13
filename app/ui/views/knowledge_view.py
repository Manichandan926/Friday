from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QLineEdit, QFrame, QComboBox, QTextEdit
)
from PySide6.QtCore import Qt
from app.memory.memory_manager import MemoryManager

class KnowledgeView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # header
        header = QLabel("Knowledge Vault", self)
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #818cf8;")
        layout.addWidget(header)

        # add knowledge form
        form_card = QFrame(self)
        form_card.setObjectName("Card")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(15, 15, 15, 15)
        form_layout.setSpacing(10)

        form_title = QLabel("Add Knowledge", form_card)
        form_title.setStyleSheet("font-weight: bold; color: #10b981;")
        form_layout.addWidget(form_title)

        row1 = QHBoxLayout()
        self.title_input = QLineEdit(form_card)
        self.title_input.setPlaceholderText("Title")

        self.category_select = QComboBox(form_card)
        self.category_select.addItems(["general", "aws", "dsa", "interview", "project", "course"])
        self.category_select.setCurrentText("general")

        self.tags_input = QLineEdit(form_card)
        self.tags_input.setPlaceholderText("Tags (comma-separated)")

        row1.addWidget(self.title_input)
        row1.addWidget(self.category_select)
        row1.addWidget(self.tags_input)
        form_layout.addLayout(row1)

        self.content_input = QTextEdit(form_card)
        self.content_input.setPlaceholderText("Write your notes here...")
        self.content_input.setMaximumHeight(100)
        form_layout.addWidget(self.content_input)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Save to Vault", form_card)
        add_btn.setObjectName("PrimaryBtn")
        add_btn.clicked.connect(self.add_item)
        btn_row.addStretch()
        btn_row.addWidget(add_btn)
        form_layout.addLayout(btn_row)

        layout.addWidget(form_card)

        # search bar
        search_row = QHBoxLayout()
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Search knowledge vault...")
        self.search_input.returnPressed.connect(self.search)

        search_btn = QPushButton("Search", self)
        search_btn.setObjectName("PrimaryBtn")
        search_btn.clicked.connect(self.search)

        clear_btn = QPushButton("Show All", self)
        clear_btn.clicked.connect(self.refresh)

        search_row.addWidget(self.search_input)
        search_row.addWidget(search_btn)
        search_row.addWidget(clear_btn)
        layout.addLayout(search_row)

        # results table
        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Title", "Category", "Content", "Tags", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        self.table.clearContents()
        self.table.setRowCount(0)

        items = MemoryManager.get_knowledge_items()
        self._populate_table(items)

    def search(self):
        query = self.search_input.text().strip()
        if not query:
            self.refresh()
            return

        results = MemoryManager.search_knowledge(query)
        self.table.clearContents()
        self.table.setRowCount(0)
        self._populate_table(results)

    def _populate_table(self, items):
        self.table.setRowCount(len(items))

        for row, item in enumerate(items):
            self.table.setItem(row, 0, QTableWidgetItem(item.title))

            cat_item = QTableWidgetItem(item.category.upper())
            cat_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, cat_item)

            preview = item.content[:150] + "..." if len(item.content) > 150 else item.content
            self.table.setItem(row, 2, QTableWidgetItem(preview))
            self.table.setItem(row, 3, QTableWidgetItem(item.tags or ""))

            del_btn = QPushButton("Delete", self.table)
            del_btn.setObjectName("DangerBtn")
            del_btn.setStyleSheet("padding: 2px 10px; font-size: 11px;")
            del_btn.setProperty("item_id", item.id)
            del_btn.clicked.connect(self.delete_item)
            self.table.setCellWidget(row, 4, del_btn)

    def add_item(self):
        title = self.title_input.text().strip()
        content = self.content_input.toPlainText().strip()
        if not title or not content:
            return

        category = self.category_select.currentText()
        tags = self.tags_input.text().strip() or None

        MemoryManager.add_knowledge_item(title=title, category=category, content=content, tags=tags)

        self.title_input.clear()
        self.content_input.clear()
        self.tags_input.clear()
        self.refresh()

    def delete_item(self):
        btn = self.sender()
        if btn:
            item_id = btn.property("item_id")
            MemoryManager.delete_knowledge_item(item_id)
            self.refresh()
