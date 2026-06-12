# Global QSS Stylesheet for FRIDAY
# Color Palette: Deep Slate (#0b0f19), Card Dark (#1e293b), Indigo Accent (#6366f1), Teal (#10b981)

DARK_STYLESHEET = """
/* Global defaults */
QWidget {
    background-color: #0b0f19;
    color: #f8fafc;
    font-family: 'Segoe UI', 'Outfit', 'Inter', sans-serif;
    font-size: 13px;
}

/* Sidebar navigation */
QFrame#Sidebar {
    background-color: #030712;
    border-right: 1px solid #1f2937;
    min-width: 200px;
    max-width: 200px;
}

QLabel#SidebarTitle {
    color: #818cf8;
    font-size: 20px;
    font-weight: bold;
    padding: 20px 10px;
}

/* Sidebar Navigation Buttons */
QPushButton#SidebarBtn {
    background-color: transparent;
    color: #9ca3af;
    border: none;
    border-radius: 6px;
    padding: 10px 15px;
    text-align: left;
    font-weight: 500;
}

QPushButton#SidebarBtn:hover {
    background-color: #111827;
    color: #f3f4f6;
}

QPushButton#SidebarBtn:checked {
    background-color: #1e1b4b;
    color: #c7d2fe;
    border-left: 3px solid #6366f1;
}

/* Header Area */
QFrame#Header {
    background-color: #030712;
    border-bottom: 1px solid #1f2937;
    max-height: 60px;
    min-height: 60px;
}

QLabel#HeaderTitle {
    font-size: 16px;
    font-weight: bold;
    color: #f3f4f6;
}

/* Cards & Content containers */
QFrame#Card {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 8px;
}

QFrame#Card:hover {
    border: 1px solid #374151;
}

/* Text edits & Inputs */
QLineEdit, QTextEdit {
    background-color: #1f2937;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 8px 12px;
    color: #f9fafb;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #6366f1;
}

/* Primary buttons */
QPushButton#PrimaryBtn {
    background-color: #6366f1;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}

QPushButton#PrimaryBtn:hover {
    background-color: #4f46e5;
}

QPushButton#PrimaryBtn:pressed {
    background-color: #4338ca;
}

/* Secondary/Danger buttons */
QPushButton#DangerBtn {
    background-color: #ef4444;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}

QPushButton#DangerBtn:hover {
    background-color: #dc2626;
}

/* Scrollbars */
QScrollBar:vertical {
    border: none;
    background: #0b0f19;
    width: 8px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #374151;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #4b5563;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* Table styling */
QTableWidget {
    background-color: #111827;
    border: 1px solid #1f2937;
    gridline-color: #1f2937;
    border-radius: 6px;
}

QHeaderView::section {
    background-color: #1f2937;
    color: #9ca3af;
    padding: 6px;
    border: 1px solid #1f2937;
    font-weight: bold;
}

/* List widget styling */
QListWidget {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 6px;
}

QListWidget::item {
    padding: 10px;
    border-bottom: 1px solid #1f2937;
}

QListWidget::item:hover {
    background-color: #1f2937;
}

QListWidget::item:selected {
    background-color: #1e1b4b;
    color: #c7d2fe;
}
"""
