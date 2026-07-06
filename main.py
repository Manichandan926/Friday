"""
main.py — FRIDAY's entry point.

Terminal-first: `python main.py` drops straight into the chat loop with no
Qt imported (saves ~22MB RSS on an 8GB machine). The desktop dashboard is
opt-in via `python main.py --ui`.
"""
import argparse
import asyncio
import sys

from app.core.assistant import FridayAssistant
from app.core.logger import logger
from app.memory.backup import secure_environment_files
from app.memory.database import init_db
from app.memory.memory_manager import MemoryManager
from app.scheduler.service import FridayScheduler

# ANSI styling, disabled when not talking to a real terminal.
if sys.stdout.isatty():
    BOLD, CYAN, DIM, RESET = "\033[1m", "\033[36m", "\033[2m", "\033[0m"
else:
    BOLD = CYAN = DIM = RESET = ""


def _banner(assistant: FridayAssistant) -> str:
    from app.core.config import settings
    from app.core.native_bridge import daemon_kind

    provider = assistant.provider_name or settings.DEFAULT_LLM_PROVIDER
    model = getattr(assistant.provider, "model", None) or "not configured"
    watcher = {"rust": "rust watcher", "c": "C monitor", None: "python fallback"}[daemon_kind()]

    line = "─" * 56
    return (
        f"{DIM}{line}{RESET}\n"
        f"  {BOLD}FRIDAY{RESET} — your assistant, on your machine\n"
        f"  {DIM}brain: {provider} ({model})   body: {watcher}{RESET}\n"
        f"  {DIM}/help commands · /cost session usage · exit to quit{RESET}\n"
        f"{DIM}{line}{RESET}"
    )


async def terminal_chat() -> None:
    convs = MemoryManager.get_conversations()
    if convs:
        conv = convs[0]
    else:
        conv = MemoryManager.create_conversation("Chat with FRIDAY")

    assistant = FridayAssistant()
    print(_banner(assistant))

    while True:
        try:
            # input() runs in a worker thread so background tasks (memory
            # extraction, summary folding) keep running while FRIDAY waits.
            user_input = (await asyncio.to_thread(input, f"{BOLD}You:{RESET} ")).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{DIM}Bye — see you.{RESET}")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print(f"{DIM}Bye — see you.{RESET}")
            break

        print(f"{DIM}…{RESET}", end="\r")
        try:
            response = await assistant.chat(conv.id, user_input)
        except Exception as e:
            logger.error(f"Error during chat turn: {e}")
            response = f"I hit an error handling that: {e}"
        print(f"{CYAN}{BOLD}FRIDAY:{RESET} {response}\n")

    # let just-spawned background tasks (memory/summary) finish briefly
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="FRIDAY Assistant")
    parser.add_argument("--ui", action="store_true", help="Open the Qt desktop dashboard")
    parser.add_argument("--headless", action="store_true",
                        help="Run the always-on body only (scheduler + watcher, no chat) — for systemd")
    parser.add_argument("--no-ui", action="store_true", help=argparse.SUPPRESS)  # legacy; terminal is the default
    args = parser.parse_args()

    # Interactive terminal chat is the only mode that shares stdout with the
    # user; silence console logging there so it doesn't interleave with the
    # conversation (the file log still records everything). --headless is a
    # service with no chat to protect, and --ui has its own window, so both
    # keep console logging.
    interactive = not args.ui and not args.headless
    if interactive:
        from app.core.logger import disable_console_logging
        disable_console_logging()

    logger.info("Starting FRIDAY Core Service...")

    init_db()

    # enforce file permissions (chmod 600) on secrets
    secure_environment_files()

    # start the native watcher daemon (Rust preferred, C fallback)
    try:
        from app.core.native_bridge import is_daemon_running, start_daemon
        if not is_daemon_running():
            if start_daemon():
                logger.info("Native watcher daemon started.")
            else:
                logger.info("Native daemon not available, using Python fallback.")
        else:
            logger.info("Native watcher daemon already running.")
    except Exception as e:
        logger.debug(f"Native daemon init skipped: {e}")

    # periodic jobs (email sync, health checks, deadline alerts)
    scheduler = FridayScheduler()
    scheduler.start()

    try:
        if args.ui:
            # Qt imported only here — the terminal path never pays for it.
            from PySide6.QtWidgets import QApplication
            from app.ui.main_window import MainWindow
            logger.info("Starting FRIDAY Desktop Dashboard UI...")
            app = QApplication(sys.argv)
            window = MainWindow()  # noqa: F841 — shows itself
            sys.exit(app.exec())
        elif args.headless:
            # Body only: background jobs keep running until SIGTERM/SIGINT.
            import signal
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so `finally` runs under systemd
            logger.info("Running headless — scheduler and watcher active, no chat.")
            signal.pause()
        else:
            asyncio.run(terminal_chat())
    finally:
        scheduler.shutdown()
        logger.info("FRIDAY Core Service shut down successfully.")


if __name__ == "__main__":
    main()
