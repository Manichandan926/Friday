# FRIDAY — Personal AI Assistant

FRIDAY is a terminal-first personal assistant for Linux: a cloud LLM brain
driving a lean local body — an agentic tool loop, a risk-tiered permission
system, and a native Rust watcher daemon. Built for and tested on an 8GB
Fedora KDE laptop; staying lean is a design constraint, not a phase.

## How it works

```
You (terminal)
    ↓
FridayAssistant — agentic tool loop (max 6 rounds)
    ↓ the model decides which tools it needs
toolkit.execute() — the permission choke point
    ├── Tier 1 AUTO     reads, DB writes, file watching → runs, audit-logged
    ├── Tier 2 CONFIRM  file writes, write-capable shell → pauses, asks you
    └── Tier 3 NEVER    deletion, privilege escalation  → refused, always
    ↓
friday_watcher (Rust, ~3MB RSS) — system stats + inotify over a Unix socket
```

- **Provider-agnostic brain** — Groq (default), OpenAI, Gemini, or Claude;
  swap live with `/provider <name>`. Tool calls, token usage, and cost are
  logged per call (`/cost` shows session totals).
- **Context engine** — rolling conversation summary + selective memory
  recall keep prompts small; the stable system prompt is cache-friendly.
- **Tiered permissions in code, not prompt** — the model physically cannot
  delete files or escalate: those tools don't exist, and the shell gate
  classifies every command. Tier 2 actions show a ⏸ proposal and wait for
  your "yes".
- **Native watcher** — `native/watcher/` (Rust) serves SYSINFO / PROCS /
  HEALTH / BATTERY / NETWORK / TEMPS plus WATCH/EVENTS file monitoring.
  The legacy C monitor (`native/friday_monitor.c`) remains as fallback.
- **Gmail + trackers** — OAuth2 inbox sync, placement/application tracking
  with deadline alerts, tasks, projects, and a knowledge vault in SQLite.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # add your API key (Groq free tier works)

# build the native watcher (optional but recommended)
cd native/watcher && cargo build --release && cd ../..

python main.py              # terminal chat (default)
python main.py --ui         # Qt dashboard, only loads Qt when asked
```

Just talk to it — "how much RAM am I using?", "watch my Downloads folder",
"save a note that…". Slash commands are free shortcuts that skip the LLM;
type `/help` for the list.

## Auto-start on boot

```bash
cp friday.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now friday.service
```

## Tech stack

Python 3.12+ · SQLAlchemy/SQLite · APScheduler · Rust (watcher daemon) ·
PySide6 (opt-in dashboard) · Groq/OpenAI/Gemini/Anthropic APIs · Gmail API

## Project structure

```
FRIDAY/
├── main.py                    # entry point: terminal chat, --ui for Qt
├── app/
│   ├── core/
│   │   ├── assistant.py       # agentic tool loop + approval flow
│   │   ├── toolkit.py         # tool registry + execution gate
│   │   ├── tiers.py           # the visible risk-tier config
│   │   ├── context.py         # rolling summary + memory recall
│   │   ├── native_bridge.py   # Unix-socket client for the watcher
│   │   ├── tools.py           # data readers (system, DB)
│   │   └── shell.py           # whitelisted shell executor
│   ├── llm/
│   │   ├── provider.py        # Groq/OpenAI/Gemini/Claude adapters
│   │   ├── types.py           # neutral ToolSpec/ToolCall/LLMReply
│   │   └── costs.py           # per-call token/cost accounting
│   ├── agents/                # planner, task, placement, memory agents
│   ├── email/                 # OAuth2 Gmail sync + LLM classifier
│   ├── memory/                # SQLAlchemy models + manager + backups
│   ├── scheduler/             # APScheduler background jobs
│   └── ui/                    # PySide6 dashboard (loaded only with --ui)
├── native/
│   ├── watcher/               # Rust daemon: stats + inotify (~3MB RSS)
│   └── friday_monitor.c       # legacy C monitor (fallback)
├── docs/adr/                  # architecture decision records
└── tests/                     # pytest suite
```

Phases 2.8–3.3 built an actor/event-sourcing substrate that is currently
**frozen** — tested but not wired into the chat path. See
`docs/adr/ADR-006-phase1-freeze.md`.

## Documentation

- [`docs/SETUP.md`](docs/SETUP.md) — install, configure, run (start here)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the pieces fit and why
- [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md) — the tier model, adding a
  tool, and known gaps
- [`docs/adr/`](docs/adr/) — decision records (why tiers live in the
  executor, why Rust, why cloud-brain/local-body, …)

## License

MIT
