# FRIDAY — Personal AI Assistant

FRIDAY is a desktop AI assistant that runs as a background service on Linux, providing intelligent email analysis, task management, placement tracking, and a personal knowledge vault.

## Features

- **Tool-Grounded LLM Chat** — queries are routed through real data tools before hitting the LLM, preventing hallucination
- **Gmail Integration** — OAuth2-based inbox sync with automatic priority classification
- **Placement Tracker** — auto-extracts job/internship applications from emails with deadline risk detection
- **Knowledge Vault** — store and search personal notes, interview prep, AWS topics
- **Project Tracking** — track projects with progress bars and milestones
- **Internship Scanner** — BeautifulSoup-based web scraper for career pages
- **PySide6 Dashboard** — desktop GUI with live KPI cards, system stats, and daily briefing
- **Background Scheduler** — periodic email sync, task reminders, and database backups

## Architecture

```
User Input
    ↓
Intent Router
    ├── /brief, /plan, /placements     → Direct handlers (no LLM)
    ├── "what is my RAM?"              → Tool reads /proc → injects into LLM
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
# edit .env with your Groq/OpenAI API key

# configure Gmail (place credentials.json from Google Cloud Console)
# run first time to complete OAuth flow

# start desktop dashboard
python main.py

# or start in CLI mode
python main.py --no-ui
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
| `/system` | System info (RAM, CPU, disk) |
| `/knowledge` | View knowledge vault |
| `/learn cat \| title \| content` | Add knowledge |
| `/search query` | Search knowledge |
| `/projects` | Project progress |
| `/addproject name \| desc \| %` | Track a project |
| `/plan request` | Generate task plan |
| `/help` | Show all commands |

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
├── app/
│   ├── core/
│   │   ├── assistant.py       # intent router + LLM chat
│   │   ├── tools.py           # real data tools (system, db)
│   │   ├── config.py          # settings from .env
│   │   └── logger.py          # rotating file logger
│   ├── agents/
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
│       └── views/             # dashboard panels
└── requirements.txt
```

## License

MIT
