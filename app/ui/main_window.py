from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QStackedWidget, QFrame, 
    QSystemTrayIcon, QMenu
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QCloseEvent

from app.ui.stylesheet import DARK_STYLESHEET
from app.ui.views.home_view import HomeView
from app.ui.views.chat_view import ChatView
from app.ui.views.emails_view import EmailsView
from app.ui.views.memory_view import MemoryView
from app.ui.views.tasks_view import TasksView
from app.ui.views.settings_view import SettingsView
from app.core.logger import logger

def create_system_icon() -> QIcon:
    """Programmatically draws a high-DPI violet tray icon for FRIDAY."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    
    # Outer circle (Accent Violet)
    painter.setBrush(QColor("#6366f1"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, 28, 28)
    
    # Letter 'F' in white
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(18)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "F")
    painter.end()
    
    return QIcon(pixmap)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FRIDAY Dashboard")
        self.resize(950, 600)
        self.setStyleSheet(DARK_STYLESHEET)
        
        self.init_ui()
        self.init_tray()
        self.show()

    def init_ui(self):
        # Central Main Widget
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Left Sidebar
        self.sidebar = QFrame(central_widget)
        self.sidebar.setObjectName("Sidebar")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(5)

        # Title Label
        sidebar_title = QLabel("FRIDAY", self.sidebar)
        sidebar_title.setObjectName("SidebarTitle")
        sidebar_layout.addWidget(sidebar_title)

        # Navigation Buttons
        self.nav_buttons = []
        self.views_config = [
            ("Home", HomeView),
            ("Chat", ChatView),
            ("Emails", EmailsView),
            ("Tasks", TasksView),
            ("Memory", MemoryView),
            ("Settings", SettingsView)
        ]

        for i, (name, _) in enumerate(self.views_config):
            btn = QPushButton(name, self.sidebar)
            btn.setObjectName("SidebarBtn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(self.navigate_to_index)
            sidebar_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        # Set default active
        self.nav_buttons[0].setChecked(True)

        sidebar_layout.addStretch()
        main_layout.addWidget(self.sidebar)

        # 2. Right Pane: Header + QStackedWidget content
        content_pane = QVBoxLayout()
        content_pane.setContentsMargins(0, 0, 0, 0)
        content_pane.setSpacing(0)

        # Header bar
        self.header = QFrame(central_widget)
        self.header.setObjectName("Header")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        self.header_title = QLabel("Home Status", self.header)
        self.header_title.setObjectName("HeaderTitle")
        header_layout.addWidget(self.header_title)
        header_layout.addStretch()
        
        # Mini Close/Tray button info label
        tray_hint = QLabel("Close hides to System Tray", self.header)
        tray_hint.setStyleSheet("color: #4b5563; font-size:11px;")
        header_layout.addWidget(tray_hint)

        content_pane.addWidget(self.header)

        # Stacked Widget for Views
        self.stacked_widget = QStackedWidget(central_widget)
        for _, view_class in self.views_config:
            view_instance = view_class(self.stacked_widget)
            self.stacked_widget.addWidget(view_instance)
            
        content_pane.addWidget(self.stacked_widget)
        main_layout.addLayout(content_pane)

    def init_tray(self):
        """Setup system tray icon and background controls."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(create_system_icon())
        self.tray_icon.setToolTip("FRIDAY Assistant")

        # Context Menu
        tray_menu = QMenu()
        restore_action = tray_menu.addAction("Show Dashboard")
        restore_action.triggered.connect(self.showNormal)
        restore_action.triggered.connect(self.activateWindow)
        
        exit_action = tray_menu.addAction("Exit FRIDAY")
        exit_action.triggered.connect(self.quit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        # Handle double click on tray icon to restore
        self.tray_icon.activated.connect(self.on_tray_activated)

    def navigate_to_index(self):
        btn = self.sender()
        if btn:
            btn_text = btn.text()
            for index, (name, _) in enumerate(self.views_config):
                if name == btn_text:
                    self.stacked_widget.setCurrentIndex(index)
                    self.header_title.setText(f"{name} View")
                    # Trigger refresh if view supports it
                    active_view = self.stacked_widget.currentWidget()
                    if hasattr(active_view, "refresh"):
                        active_view.refresh()
                    break

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.activateWindow()

    def closeEvent(self, event: QCloseEvent):
        """Intercept main window closing, hiding it to tray instead."""
        if self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                "FRIDAY Active",
                "FRIDAY service continues to run in the background.",
                create_system_icon(),
                2000
            )
            event.ignore()
        else:
            event.accept()

    def quit_app(self):
        """Full termination of background tray icon and GUI."""
        logger.info("Exiting FRIDAY application from system tray command.")
        self.tray_icon.hide()
        # Allows application to close normally
        self.destroy()
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.quit()
