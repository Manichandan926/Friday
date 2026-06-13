from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QPushButton, QLineEdit, QSlider, QProgressBar, QScrollArea
)
from PySide6.QtCore import Qt, QTimer
from app.memory.memory_manager import MemoryManager

class ProjectsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # header
        header = QLabel("Project Tracker", self)
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #818cf8;")
        layout.addWidget(header)

        # add project form
        form_card = QFrame(self)
        form_card.setObjectName("Card")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(15, 15, 15, 15)
        form_layout.setSpacing(10)

        form_title = QLabel("Add Project", form_card)
        form_title.setStyleSheet("font-weight: bold; color: #10b981;")
        form_layout.addWidget(form_title)

        row = QHBoxLayout()
        self.name_input = QLineEdit(form_card)
        self.name_input.setPlaceholderText("Project name")

        self.desc_input = QLineEdit(form_card)
        self.desc_input.setPlaceholderText("Description (optional)")

        add_btn = QPushButton("Add Project", form_card)
        add_btn.setObjectName("PrimaryBtn")
        add_btn.clicked.connect(self.add_project)

        row.addWidget(self.name_input)
        row.addWidget(self.desc_input)
        row.addWidget(add_btn)
        form_layout.addLayout(row)
        layout.addWidget(form_card)

        # scroll area for project cards
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()

        scroll.setWidget(self.cards_container)
        layout.addWidget(scroll)

        self.refresh()

    def refresh(self):
        # clear existing cards
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        projects = MemoryManager.get_projects()

        for p in projects:
            card = self._create_project_card(p)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def _create_project_card(self, project):
        card = QFrame(self)
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 15, 15, 15)
        card_layout.setSpacing(8)

        # top row: name + status
        top_row = QHBoxLayout()
        name_label = QLabel(f"📁 {project.name}", card)
        name_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #f3f4f6;")
        top_row.addWidget(name_label)

        status_colors = {"active": "#10b981", "paused": "#f59e0b", "completed": "#6366f1"}
        status_color = status_colors.get(project.status, "#9ca3af")
        status_label = QLabel(project.status.upper(), card)
        status_label.setStyleSheet(
            f"color: {status_color}; font-weight: bold; padding: 2px 10px; "
            f"border: 1px solid {status_color}; border-radius: 10px; font-size: 11px;"
        )
        top_row.addStretch()
        top_row.addWidget(status_label)
        card_layout.addLayout(top_row)

        # description
        if project.description:
            desc = QLabel(project.description, card)
            desc.setStyleSheet("color: #9ca3af; font-size: 12px;")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

        # progress bar
        progress_row = QHBoxLayout()
        progress_bar = QProgressBar(card)
        progress_bar.setValue(project.progress)
        progress_bar.setTextVisible(False)
        progress_bar.setMaximumHeight(8)
        progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1f2937;
                border-radius: 4px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #6366f1;
                border-radius: 4px;
            }
        """)

        pct_label = QLabel(f"{project.progress}%", card)
        pct_label.setStyleSheet("color: #818cf8; font-weight: bold; font-size: 13px;")

        progress_row.addWidget(progress_bar)
        progress_row.addWidget(pct_label)
        card_layout.addLayout(progress_row)

        # update controls
        ctrl_row = QHBoxLayout()

        slider = QSlider(Qt.Horizontal, card)
        slider.setRange(0, 100)
        slider.setValue(project.progress)
        slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #1f2937; height: 6px; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #6366f1; width: 14px; margin: -4px 0; border-radius: 7px;
            }
        """)
        slider.setProperty("project_id", project.id)
        slider.setProperty("progress_bar", progress_bar)
        slider.setProperty("pct_label", pct_label)
        slider.valueChanged.connect(self._on_slider_change)

        update_btn = QPushButton("Save Progress", card)
        update_btn.setObjectName("PrimaryBtn")
        update_btn.setStyleSheet("padding: 4px 12px; font-size: 11px;")
        update_btn.setProperty("project_id", project.id)
        update_btn.setProperty("slider", slider)
        update_btn.clicked.connect(self._save_progress)

        ctrl_row.addWidget(slider)
        ctrl_row.addWidget(update_btn)
        card_layout.addLayout(ctrl_row)

        return card

    def _on_slider_change(self, value):
        slider = self.sender()
        if slider:
            bar = slider.property("progress_bar")
            label = slider.property("pct_label")
            if bar:
                bar.setValue(value)
            if label:
                label.setText(f"{value}%")

    def _save_progress(self):
        btn = self.sender()
        if btn:
            project_id = btn.property("project_id")
            slider = btn.property("slider")
            if slider:
                progress = slider.value()
                status = "completed" if progress >= 100 else "active"
                MemoryManager.update_project(project_id, progress=progress, status=status)

    def add_project(self):
        name = self.name_input.text().strip()
        if not name:
            return

        desc = self.desc_input.text().strip() or None
        MemoryManager.add_project(name=name, description=desc, progress=0)

        self.name_input.clear()
        self.desc_input.clear()
        self.refresh()
