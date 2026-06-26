"""
observability_view.py — Performance Dashboard for FRIDAY.

Reads the internal MetricsRegistry and displays Mailbox depth,
Event rate, Actor restarts, and other kernel metrics. No external dependencies.
"""

import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QFrame
)
from PySide6.QtCore import QTimer, Qt

from app.core.metrics import MetricsRegistry


class ObservabilityView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.metrics_registry = MetricsRegistry.get_instance()
        
        self._setup_ui()
        
        # Poll metrics every 1 second
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_metrics)
        self.timer.start(1000)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("System Kernel Observability")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #E0E0E0;")
        layout.addWidget(title)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        container = QWidget()
        self.metrics_layout = QVBoxLayout(container)
        self.metrics_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _refresh_metrics(self) -> None:
        # Clear existing widgets
        while self.metrics_layout.count():
            item = self.metrics_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        # Fetch metrics
        data = self.metrics_registry.export_json()
        
        if not data:
            lbl = QLabel("No telemetry data collected yet.")
            lbl.setStyleSheet("color: #888;")
            self.metrics_layout.addWidget(lbl)
            return
            
        for key, info in data.items():
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background-color: #2A2A2A;
                    border-radius: 8px;
                    padding: 10px;
                    margin-bottom: 5px;
                }
            """)
            clayout = QVBoxLayout(card)
            
            title = QLabel(key)
            title.setStyleSheet("font-weight: bold; font-size: 16px; color: #4CAF50;")
            
            desc = QLabel(info.get("description", ""))
            desc.setStyleSheet("color: #AAA; font-size: 12px;")
            
            val_text = json.dumps(info, indent=2)
            # Simplistic parsing for display
            if info["type"] == "gauge":
                val_text = f"Value: {info.get('latest')} | Labels: {info.get('labels')}"
            elif info["type"] == "counter":
                val_text = f"Counts: {info.get('counts')}"
                
            val = QLabel(val_text)
            val.setStyleSheet("font-family: monospace; color: #DDD;")
            val.setWordWrap(True)
            
            clayout.addWidget(title)
            clayout.addWidget(desc)
            clayout.addWidget(val)
            
            self.metrics_layout.addWidget(card)
