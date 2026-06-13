# FRIDAY — Personal AI Assistant

FRIDAY is a desktop AI assistant that runs as a background service on Linux, providing intelligent email analysis, task management, placement tracking, terminal access, and a personal knowledge vault.

## Features

- **Tool-Grounded LLM Chat** — queries are routed through real data tools before hitting the LLM, preventing hallucination
- **Safe Terminal Access** — sandboxed shell executor with whitelist/blacklist; FRIDAY autonomously picks commands to answer system questions
- **Gmail Integration** — OAuth2-based inbox sync with automatic priority classification
- **Placement Tracker** — auto-extracts job/internship applications from emails with deadline risk detection
- **Knowledge Vault** — store, search, and manage personal notes, interview prep, AWS topics
- **Project Tracking** — track projects with progress bars and milestones
- **Proactive Monitoring** — deadline alerts, system health checks, auto-notifications
- **Internship Scanner** — BeautifulSoup-based web scraper for career pages
- **PySide6 Dashboard** — desktop GUI with live KPI cards, system stats, and daily briefing
- **Background Scheduler** — periodic email sync, task reminders, health checks, and database backups
- **Auto-Start Service** — systemd user service file for boot-time startup

## Architecture

```
User Input
    ↓
Intent Router
    ├── /brief, /plan, /placements     → Direct handlers (no LLM)
    ├── "what is my RAM?"              → Tool reads /proc → injects into LLM
    ├── "what is using my RAM?"        → Shell Agent → ps aux → injects into LLM
    ├── "show my emails"               → Tool reads SQLite → injects into LLM
    └── "explain binary search"        → Pure LLM (general knowledge)
```

## Quick Start

```bash
# create virtual environment
python -m venv .venv
source .venv/bin/activate

# install dependencies
pip install -r requirements.txt

# configure API keys
cp .env.example .env
# edit .env with your Groq/OpenAI/Gemini API key

# start desktop dashboard
python main.py

# or start in CLI mode
python main.py --no-ui
```

## Auto-Start on Boot

```bash
# install as systemd user service
cp friday.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable friday.service
systemctl --user start friday.service
```

## Commands

| Command | Description |
| --- | --- |
| `/brief` | Daily briefing |
| `/emails` | Email inbox summary |
| `/tasks` | Pending tasks |
| `/placements` | Application tracker |
| `/deadlines` | Deadline risk alerts |
| `/interviews` | Active interviews |
| `/scan` | Scan web for internships |
| `/run command` | Execute a safe shell command |
| `/notifications` | View proactive alerts |
| `/system` | System info (RAM, CPU, disk) |
| `/knowledge` | View knowledge vault |
| `/learn cat \| title \| content` | Add knowledge |
| `/search query` | Search knowledge |
| `/projects` | Project progress |
| `/addproject name \| desc \| %` | Track a project |
| `/plan request` | Generate task plan |
| `/help` | Show all commands |

## Dashboard Views

| View | What it shows |
| --- | --- |
| **Home** | KPI cards, system stats, daily brief |
| **Chat** | LLM conversation with tool routing |
| **Emails** | Gmail inbox with priority badges |
| **Tasks** | Task list with create/complete actions |
| **Knowledge** | Knowledge vault with add/search/delete |
| **Projects** | Project cards with progress bars |
| **Memory** | Long-term facts and preferences |
| **Settings** | API keys and provider config |

## Tech Stack

- **Python 3.12+**
- **PySide6** — Desktop UI
- **SQLAlchemy** — ORM + SQLite
- **APScheduler** — Background jobs
- **Groq/OpenAI/Gemini** — LLM providers
- **Google Gmail API** — Email integration
- **BeautifulSoup4** — Web scraping

## Project Structure

```
FRIDAY/
├── main.py                    # entry point
├── friday.service             # systemd auto-start
├── app/
│   ├── core/
│   │   ├── assistant.py       # intent router + LLM chat
│   │   ├── tools.py           # real data tools (system, db)
│   │   ├── shell.py           # sandboxed terminal executor
│   │   ├── config.py          # settings from .env
│   │   └── logger.py          # rotating file logger
│   ├── agents/
│   │   ├── shell_agent.py     # LLM → command → execute
│   │   ├── planner_agent.py   # daily briefing generator
│   │   ├── task_agent.py      # plan-to-tasks converter
│   │   ├── placement_agent.py # email → application extractor
│   │   └── memory_agent.py    # conversation fact learner
│   ├── email/
│   │   ├── gmail.py           # OAuth2 Gmail connector
│   │   └── analyzer.py        # LLM email classifier
│   ├── plugins/
│   │   └── internship_scanner/
│   │       └── scanner.py     # BeautifulSoup web scraper
│   ├── memory/
│   │   ├── models.py          # SQLAlchemy ORM models
│   │   ├── memory_manager.py  # database CRUD operations
│   │   ├── database.py        # engine + session setup
│   │   └── backup.py          # automated backups
│   ├── scheduler/
│   │   └── service.py         # APScheduler background jobs
│   └── ui/
│       ├── main_window.py     # PySide6 main window + tray
│       ├── stylesheet.py      # dark theme CSS
│       └── views/
│           ├── home_view.py
│           ├── chat_view.py
│           ├── emails_view.py
│           ├── tasks_view.py
│           ├── knowledge_view.py
│           ├── projects_view.py
│           ├── memory_view.py
│           └── settings_view.py
└── requirements.txt
```

## License

MIT
