# FRIDAY Assistant: Complete User & Developer Manual

FRIDAY is a personal desktop AI assistant built specifically for Linux (optimized for Fedora KDE). This manual describes all user-facing functionalities, slash commands, interface views, background workflows, optimization layers, and developer integration guidelines.

---

## 1. User Interface & Dashboards

FRIDAY features a PySide6 GUI desktop interface styled with a dark-mode theme.

*   **Header Telemetry**: The top bar of the application displays real-time telemetry (CPU load, RAM usage, Battery level, and charging status) refreshed dynamically every 30 seconds.
*   **Sidebar Navigation**: A collapsible menu allowing instant switching between functional screens.
*   **View Modules**:
    *   **Home Dashboard**: Consolidates active project progress bars, upcoming task due dates, critical job deadlines, and unread notification counts.
    *   **Chat Center**: A standard conversation box containing the LLM interface with prompt guidance.
    *   **Gmail View**: Displays unread high-priority and medium-priority emails along with AI-generated action summaries.
    *   **Kanban / Tasks**: Interactive dashboard for creating, sorting, completing, and prioritizing todo items.
    *   **Placement Tracker**: Visual pipeline for internship and job applications, categorized by pipeline stage.
    *   **Knowledge Vault**: Interactive repository for notes, study guides, and interview prep sheets.
    *   **Settings Panel**: Manage LLM model selection, enter API keys (Gemini, Groq, OpenAI), configure directory paths, and toggle background jobs.

---

## 2. Interactive Slash Commands

When typing into the Chat box or using the CLI runner (`main.py --no-ui`), these direct commands bypass LLM generation, saving API costs and ensuring instant execution:

| Command | Usage Example | Functional Output |
|---|---|---|
| `/help` | `/help` | Displays the interactive help table of commands. |
| `/brief` | `/brief` | Generates a daily briefing card outlining tasks, high-priority emails, and focus targets. |
| `/system` | `/system` | Retrieves kernel, uptime, load avg, memory usage, and disk stats from the C daemon. |
| `/emails` | `/emails` | Lists the top 5 parsed emails stored in the database, showing AI-extracted task actions. |
| `/tasks` | `/tasks` | Renders a prioritized list of all pending todo items with due dates and overdue markers. |
| `/placements`| `/placements` | Shows placement pipeline analytics (bar graph) and application listings. |
| `/deadlines` | `/deadlines` | Sorts and lists upcoming or passed job/internship application deadlines. |
| `/interviews` | `/interviews` | Filters and displays all job applications currently in the `interviewing` stage. |
| `/scan` | `/scan` | Triggers the internship scanner plugin to search target sites for new job listings. |
| `/notifications`| `/notifications` | Displays unread system warnings, critical battery alerts, or deadline pop-ups. |
| `/knowledge` | `/knowledge` | Lists categories and titles of items saved in the reference vault. |
| `/learn` | `/learn aws \| S3 Encryption \| S3 encryption uses KMS keys.` | Saves a new reference card to the database with optional tag flags. |
| `/search` | `/search encryption` | Scans title, content, and tags in the knowledge database to return matched text snippets. |
| `/projects` | `/projects` | Lists active projects showing title, status, and progress bars. |
| `/addproject` | `/addproject Portfolio \| Personal website build \| 45` | Registers a new project with description and progress percentage. |
| `/plan` | `/plan Study AWS developer associate exam` | Decomposes a request into a step-by-step list of pending database tasks. |
| `/run` | `/run ps aux \| grep python` | Validates a shell command against the C sandbox whitelist and executes it. |

---

## 3. Sandboxed Terminal Execution (`/run`)

FRIDAY includes a sandboxed execution layer (`app/core/shell.py`) to prevent shell injection or accidental deletions.

*   **Whitelist Verification**: A dedicated library compiles 83 safe, read-only commands (e.g. `free`, `df`, `sensors`, `ip`, `systemctl`, `uname`, `grep`).
*   **Banned Syntax Checks**: Commands matching dangerous pattern identifiers are blocked immediately.
    *   *Examples of blocked commands*: `sudo`, `rm`, `dd`, `mkfs`, `chmod`, `chown`, file redirectors to system directories (`> /etc`), and pipe-to-shell patterns (`curl ... | sh`).
*   **Segment Parsing**: If you run a command pipeline (e.g., `command_a | command_b`), the validator splits the query on `|` and checks *every individual segment* against the whitelist.
*   **Permissions**: Shell commands execute with standard user permissions. System-level configuration changes are blocked.

---

## 4. Multi-Agent Ecosystem

FRIDAY utilizes modular agents that handle specific tasks:

*   **InboxAgent (`app/agents/inbox_agent.py`)**:
    *   Scans incoming email bodies.
    *   Determines priority level (`high`, `medium`, `low`).
    *   Generates a concise summary.
    *   Extracts actionable items and deadline dates.
*   **PlacementAgent (`app/agents/placement_agent.py`)**:
    *   Identifies internship-related emails.
    *   Extracts company name, job role, deadlines, and application status.
    *   Updates the job tracking pipeline database.
*   **TaskAgent (`app/agents/task_agent.py`)**:
    *   Converts natural language commands (e.g. *"Remind me to study AWS tomorrow"*) into database tasks.
*   **MemoryAgent (`app/agents/memory_agent.py`)**:
    *   Runs in the background of active chat sessions.
    *   Extracts user preferences, facts, and profile details.
    *   Updates the user profile table in the database.
*   **PlannerAgent (`app/agents/planner_agent.py`)**:
    *   Decomposes complex goals into sequential step-by-step project plans.
    *   Generates daily briefing cards.
*   **ShellAgent (`app/agents/shell_agent.py`)**:
    *   Translates natural language questions (e.g., *"What is using the most RAM?"*) into safe, whitelisted terminal commands.

---

## 5. Background Scheduler (`app/scheduler/service.py`)

A background thread pool managed by `APScheduler` runs automatically when the application starts:

1.  **Gmail Synchronizer (Every 5 minutes)**:
    *   Queries the Gmail API for new messages.
    *   Deduplicates emails using unique message IDs.
    *   Sends email content to the `InboxAgent` and `PlacementAgent` to update database tables.
    *   Dispatches desktop toast alerts for high-priority emails.
2.  **Resource Monitor (Every 30 seconds)**:
    *   Queries the C telemetry daemon.
    *   Generates alerts for high resource usage:
        *   RAM > 90%
        *   Disk space > 90%
        *   Battery < 15% (when discharging)
    *   Triggers native Linux `notify-send` desktop warnings.
3.  **Deadline Tracker (Every 30 minutes)**:
    *   Scans upcoming job application deadlines and task due dates.
    *   Generates urgent desktop alerts for items due within 24 hours.
4.  **Database Archiver (Every 24 hours)**:
    *   Creates a timestamped backup of the database in `database/backups/`.
    *   Retains the last 7 backups, automatically deleting older files.

---

## 6. Native Telemetry Subsystem (`native/`)

To minimize CPU overhead, system telemetry is gathered by a daemon written in C.

*   **Unix Domain Socket**: Communicates with the Python application over a local socket connection (`/tmp/friday_monitor.sock`).
*   **Static Telemetry Cache**: A background thread updates system statistics every 2 seconds. Mutex locks protect data structures during updates. Socket queries read directly from this memory cache, avoiding disk I/O latency.
*   **Daemon Command Interface**:
    *   `SYSINFO`: CPU load, hostname, uptime, memory usage, and kernel version.
    *   `PROCS`: Top 15 active processes sorted by memory usage.
    *   `HEALTH`: Core metrics (CPU, RAM, disk, battery percentages).
    *   `BATTERY`: Charging status, percentage, and discharge rate.
    *   `NETWORK`: Active network interfaces, status, and IP addresses.
    *   `TEMPS`: Thermal sensor readings.

---

## 7. Python ctypes C-Acceleration Library

To optimize shell validations, FRIDAY uses a compiled C shared library:

*   **Files**: `native/command_validator.c` and `app/core/fast_ops.py`.
*   **Logic**:
    *   Stores the command whitelist in a sorted array.
    *   Performs O(log n) lookups using binary search.
    *   Identifies blocked commands using fast string matching (`strstr`), avoiding regex overhead.
*   **Performance**: The C validator is approximately 38 times faster than Python regex-based validation (1.4 µs vs 54.6 µs per call).
*   **Fallback**: If the compiled library is missing, the system automatically falls back to Python regex checks.

---

## 8. Database Schema & Data Models (`app/memory/models.py`)

FRIDAY uses SQLAlchemy with a local SQLite database to manage system data:

*   **Conversations / Messages**: Tracks chat history.
*   **MemoryItems**: Stores user facts and preferences extracted by the `MemoryAgent`.
*   **Emails**: Caches parsed email subjects, summaries, senders, and priorities.
*   **Tasks**: Tracks todo lists, priority levels, and due dates.
*   **Applications**: Manages job application stages (`saved`, `applied`, `assessment`, `interviewing`, `offer`, `accepted`, `rejected`, `withdrawn`).
*   **KnowledgeItems**: Stores reference notes and study sheets.
*   **Projects / Milestones**: Tracks project completion progress.
*   **Notifications**: Caches background warnings and alerts.
