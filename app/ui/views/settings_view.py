from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QComboBox, QFrame, QMessageBox
)
from app.core.config import settings
from app.core.logger import logger
from pathlib import Path

class SettingsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # 1. Header
        header = QLabel("FRIDAY Settings & Configuration", self)
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #818cf8;")
        layout.addWidget(header)

        # 2. Config Card
        card = QFrame(self)
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(15)

        # Provider Select
        p_layout = QHBoxLayout()
        p_label = QLabel("Default LLM Provider:", card)
        p_label.setMinimumWidth(150)
        self.provider_select = QComboBox(card)
        self.provider_select.addItems(["groq", "openai", "gemini"])
        p_layout.addWidget(p_label)
        p_layout.addWidget(self.provider_select)
        card_layout.addLayout(p_layout)

        # Model Input
        m_layout = QHBoxLayout()
        m_label = QLabel("Default LLM Model:", card)
        m_label.setMinimumWidth(150)
        self.model_input = QLineEdit(card)
        m_layout.addWidget(m_label)
        m_layout.addWidget(self.model_input)
        card_layout.addLayout(m_layout)

        # Groq API Key
        groq_layout = QHBoxLayout()
        groq_label = QLabel("Groq API Key:", card)
        groq_label.setMinimumWidth(150)
        self.groq_input = QLineEdit(card)
        self.groq_input.setEchoMode(QLineEdit.Password)
        groq_layout.addWidget(groq_label)
        groq_layout.addWidget(self.groq_input)
        card_layout.addLayout(groq_layout)

        # OpenAI API Key
        oa_layout = QHBoxLayout()
        oa_label = QLabel("OpenAI API Key:", card)
        oa_label.setMinimumWidth(150)
        self.openai_input = QLineEdit(card)
        self.openai_input.setEchoMode(QLineEdit.Password)
        oa_layout.addWidget(oa_label)
        oa_layout.addWidget(self.openai_input)
        card_layout.addLayout(oa_layout)

        # Gemini API Key
        gem_layout = QHBoxLayout()
        gem_label = QLabel("Gemini API Key:", card)
        gem_label.setMinimumWidth(150)
        self.gemini_input = QLineEdit(card)
        self.gemini_input.setEchoMode(QLineEdit.Password)
        gem_layout.addWidget(gem_label)
        gem_layout.addWidget(self.gemini_input)
        card_layout.addLayout(gem_layout)

        # Logging level
        l_layout = QHBoxLayout()
        l_label = QLabel("Log Level:", card)
        l_label.setMinimumWidth(150)
        self.log_select = QComboBox(card)
        self.log_select.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        l_layout.addWidget(l_label)
        l_layout.addWidget(self.log_select)
        card_layout.addLayout(l_layout)

        # Save Button
        save_btn = QPushButton("Save & Apply Configuration", card)
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self.save_config)
        card_layout.addWidget(save_btn)

        layout.addWidget(card)
        layout.addStretch()

        # Load Current Values
        self.load_values()

    def load_values(self):
        self.provider_select.setCurrentText(settings.DEFAULT_LLM_PROVIDER)
        self.model_input.setText(settings.DEFAULT_LLM_MODEL)
        self.groq_input.setText(settings.GROQ_API_KEY)
        self.openai_input.setText(settings.OPENAI_API_KEY)
        self.gemini_input.setText(settings.GEMINI_API_KEY)
        self.log_select.setCurrentText(settings.LOG_LEVEL.upper())

    def save_config(self):
        # Read from inputs
        provider = self.provider_select.currentText()
        model = self.model_input.text().strip()
        groq_key = self.groq_input.text().strip()
        openai_key = self.openai_input.text().strip()
        gemini_key = self.gemini_input.text().strip()
        log_level = self.log_select.currentText()

        # Update in-memory settings instance
        settings.DEFAULT_LLM_PROVIDER = provider
        settings.DEFAULT_LLM_MODEL = model
        settings.GROQ_API_KEY = groq_key
        settings.OPENAI_API_KEY = openai_key
        settings.GEMINI_API_KEY = gemini_key
        settings.LOG_LEVEL = log_level

        # Persist to .env file
        env_path = settings.BASE_DIR / ".env"
        try:
            env_content = f"""# Core Configuration
LOG_LEVEL={log_level}
DATABASE_URL={settings.DATABASE_URL}

# LLM Selection
DEFAULT_LLM_PROVIDER={provider}
DEFAULT_LLM_MODEL={model}

# Provider Keys
GROQ_API_KEY={groq_key}
OPENAI_API_KEY={openai_key}
GEMINI_API_KEY={gemini_key}
"""
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(env_content)
                
            logger.info("Successfully updated and saved config parameters to .env")
            QMessageBox.information(self, "Success", "Configuration saved and applied successfully!")
        except Exception as e:
            logger.error(f"Failed to write configuration to .env: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save settings to .env file: {e}")
        
        self.load_values()
