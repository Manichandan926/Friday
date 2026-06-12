import asyncio
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QListWidget, QPushButton, QLineEdit, QTextBrowser, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal
from app.memory.memory_manager import MemoryManager
from app.core.assistant import FridayAssistant

class ChatWorker(QThread):
    """Asynchronous worker thread to run LLM operations without freezing the UI."""
    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, assistant: FridayAssistant, conv_id: int, message: str):
        super().__init__()
        self.assistant = assistant
        self.conv_id = conv_id
        self.message = message

    def run(self):
        try:
            # Run the async chat call in a dedicated thread-safe loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response = loop.run_until_complete(self.assistant.chat(self.conv_id, self.message))
            self.response_ready.emit(response)
        except Exception as e:
            self.error_occurred.emit(str(e))

class ChatView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.assistant = FridayAssistant()
        self.active_conv_id = None
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # 1. Left Sidebar: Conversations List
        left_panel = QFrame(self)
        left_panel.setObjectName("Card")
        left_panel.setMinimumWidth(180)
        left_panel.setMaximumWidth(220)
        
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        new_chat_btn = QPushButton("+ New Conversation", left_panel)
        new_chat_btn.setObjectName("PrimaryBtn")
        new_chat_btn.clicked.connect(self.start_new_conversation)
        left_layout.addWidget(new_chat_btn)

        conv_title = QLabel("Conversations", left_panel)
        conv_title.setStyleSheet("font-weight: bold; color: #9ca3af; margin-top: 10px;")
        left_layout.addWidget(conv_title)

        self.conv_list = QListWidget(left_panel)
        self.conv_list.itemClicked.connect(self.load_conversation)
        left_layout.addWidget(self.conv_list)
        
        layout.addWidget(left_panel)

        # 2. Right Panel: Active Conversation View
        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)

        # Conversation Header
        self.chat_header = QLabel("Select or start a new conversation to begin.", self)
        self.chat_header.setStyleSheet("font-size: 16px; font-weight: bold; color: #818cf8;")
        right_panel.addWidget(self.chat_header)

        # Chat Log
        self.chat_log = QTextBrowser(self)
        self.chat_log.setObjectName("Card")
        self.chat_log.setOpenExternalLinks(True)
        self.chat_log.setStyleSheet("border: 1px solid #1f2937; padding: 15px;")
        right_panel.addWidget(self.chat_log)

        # Input box row
        input_layout = QHBoxLayout()
        self.message_input = QLineEdit(self)
        self.message_input.setPlaceholderText("Ask FRIDAY anything...")
        self.message_input.returnPressed.connect(self.send_message)
        
        self.send_btn = QPushButton("Send", self)
        self.send_btn.setObjectName("PrimaryBtn")
        self.send_btn.clicked.connect(self.send_message)

        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_btn)
        right_panel.addLayout(input_layout)

        layout.addLayout(right_panel)

        # Initial loading
        self.refresh()

    def refresh(self):
        """Loads and refreshes conversation items."""
        self.conv_list.clear()
        convs = MemoryManager.get_conversations()
        
        for conv in convs:
            item = self.conv_list.addItem(conv.title)
            # Store conversation ID inside custom data field of QListWidgetItem
            self.conv_list.item(self.conv_list.count() - 1).setData(Qt.UserRole, conv.id)

        # Automatically select the first/active conversation if present
        if convs:
            if self.active_conv_id is None:
                self.active_conv_id = convs[0].id
            
            # Select item in UI list
            for i in range(self.conv_list.count()):
                if self.conv_list.item(i).data(Qt.UserRole) == self.active_conv_id:
                    self.conv_list.setCurrentRow(i)
                    self.load_messages(self.active_conv_id)
                    break
        else:
            self.chat_log.setHtml("<div style='color:#6b7280; text-align:center;'>No messages yet.</div>")

    def start_new_conversation(self):
        new_conv = MemoryManager.create_conversation("Chat Session")
        self.active_conv_id = new_conv.id
        self.refresh()
        self.message_input.setFocus()

    def load_conversation(self, item):
        conv_id = item.data(Qt.UserRole)
        self.active_conv_id = conv_id
        self.chat_header.setText(f"Active Conversation (ID: {conv_id})")
        self.load_messages(conv_id)

    def load_messages(self, conv_id):
        self.chat_log.clear()
        messages = MemoryManager.get_messages(conv_id)
        
        html_content = ""
        for msg in messages:
            if msg.role == "user":
                html_content += f"""
                <div style="margin: 10px 0; text-align: right;">
                    <div style="display: inline-block; background-color: #312e81; color: #e0e7ff; 
                                padding: 10px 14px; border-radius: 12px 12px 0 12px; max-width: 70%; text-align: left;">
                        <b>You</b><br/>{msg.content}
                    </div>
                </div>
                """
            elif msg.role == "assistant":
                # Handle formatted newlines gracefully in HTML
                formatted_reply = msg.content.replace('\n', '<br/>')
                html_content += f"""
                <div style="margin: 10px 0; text-align: left;">
                    <div style="display: inline-block; background-color: #1e293b; color: #f8fafc; 
                                padding: 10px 14px; border-radius: 12px 12px 12px 0; max-width: 70%;">
                        <b style="color: #818cf8;">FRIDAY</b><br/>{formatted_reply}
                    </div>
                </div>
                """
        
        if not html_content:
            html_content = "<div style='color:#6b7280; text-align:center; padding-top:20px;'>Ask your first question to begin conversation.</div>"
            
        self.chat_log.setHtml(html_content)
        # Scroll to bottom
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

    def send_message(self):
        text = self.message_input.text().strip()
        if not text or self.active_conv_id is None:
            return

        self.message_input.clear()
        
        # Append User Message immediately visually
        current_html = self.chat_log.toHtml()
        if "Ask your first question" in current_html:
            current_html = ""
            
        user_bubble = f"""
        <div style="margin: 10px 0; text-align: right;">
            <div style="display: inline-block; background-color: #312e81; color: #e0e7ff; 
                        padding: 10px 14px; border-radius: 12px 12px 0 12px; max-width: 70%; text-align: left;">
                <b>You</b><br/>{text}
            </div>
        </div>
        """
        
        thinking_bubble = """
        <div id="thinking-indicator" style="margin: 10px 0; text-align: left;">
            <div style="display: inline-block; background-color: #1e293b; color: #9ca3af; 
                        padding: 10px 14px; border-radius: 12px 12px 12px 0;">
                <b style="color: #818cf8;">FRIDAY</b><br/><i>Thinking...</i>
            </div>
        </div>
        """
        
        self.chat_log.setHtml(current_html + user_bubble + thinking_bubble)
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

        # Start QThread Worker
        self.worker = ChatWorker(self.assistant, self.active_conv_id, text)
        self.worker.response_ready.connect(self.handle_response)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.start()

    def handle_response(self, reply):
        # Reload all messages for the conversation to render correctly
        self.load_messages(self.active_conv_id)

    def handle_error(self, err_msg):
        # Reload messages, it will contain the error message appended as assistant response
        self.load_messages(self.active_conv_id)
