import sys
import asyncio
import argparse
from PySide6.QtWidgets import QApplication

from app.memory.database import init_db
from app.scheduler.service import FridayScheduler
from app.core.assistant import FridayAssistant
from app.core.logger import logger
from app.memory.memory_manager import MemoryManager
from app.ui.main_window import MainWindow
from app.memory.backup import secure_environment_files

async def cli_interactive_chat() -> None:
    print("\n" + "="*50)
    print("   FRIDAY INTERACTIVE CLI VERIFICATION   ")
    print("="*50)
    
    # get or create conversation context
    convs = MemoryManager.get_conversations()
    if convs:
        conv = convs[0]
        print(f"[*] Resuming conversation: '{conv.title}' (ID: {conv.id})")
    else:
        conv = MemoryManager.create_conversation("Default Verification Chat")
        print(f"[*] Created new conversation context (ID: {conv.id})")
        
    # seed initial memories if DB is fresh
    memories = MemoryManager.get_memory_items()
    if not memories:
        MemoryManager.add_memory_item("user_info", "The user's name is Mani Chandan.")
        MemoryManager.add_memory_item("user_info", "The user is building a Linux-based AI assistant named FRIDAY.")
        logger.info("Injected test memory preferences into SQLite database.")

    assistant = FridayAssistant()
    print("\nFRIDAY is ready. Type your messages below. Type 'exit' or 'quit' to end.\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                break
                
            print("FRIDAY: (thinking...)")
            response = await assistant.chat(conv.id, user_input)
            print(f"FRIDAY: {response}\n")
            
        except (KeyboardInterrupt, EOFError):
            print("\nExiting CLI session...")
            break
        except Exception as e:
            logger.error(f"Error during interactive chat: {e}")
            print(f"FRIDAY: Encountered an error processing your query: {e}\n")

def main() -> None:
    parser = argparse.ArgumentParser(description="FRIDAY Assistant CLI & Dashboard")
    parser.add_argument("--no-ui", action="store_true", help="Run interactive CLI chat verification mode")
    args = parser.parse_args()
    
    logger.info("Starting FRIDAY Core Service...")
    
    # init sqlite database tables
    init_db()
    
    # enforce file permissions (chmod 600)
    secure_environment_files()
    
    # start native C monitor daemon (if compiled)
    try:
        from app.core.native_bridge import is_daemon_running, start_daemon
        if not is_daemon_running():
            if start_daemon():
                logger.info("Native system monitor daemon started.")
            else:
                logger.info("Native monitor not available, using Python fallback.")
        else:
            logger.info("Native system monitor daemon already running.")
    except Exception as e:
        logger.debug(f"Native monitor init skipped: {e}")
    
    # start scheduler for periodic jobs
    scheduler = FridayScheduler()
    scheduler.start()
    
    try:
        if args.no_ui:
            asyncio.run(cli_interactive_chat())
        else:
            logger.info("Starting FRIDAY Desktop Dashboard UI...")
            app = QApplication(sys.argv)
            window = MainWindow()
            sys.exit(app.exec())
    finally:
        # shutdown background jobs cleanly
        scheduler.shutdown()
        logger.info("FRIDAY Core Service shut down successfully.")

if __name__ == "__main__":
    main()
