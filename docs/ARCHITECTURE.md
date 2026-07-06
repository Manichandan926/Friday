# FRIDAY Architecture

This describes the system as it exists after Phase 1 (milestones 1–5). It is
meant to let someone who has forgotten the details — or never had them —
understand the shape of the system and *why* each piece is built the way it
is. Design rationale for the big choices lives in `docs/adr/`.

## The one-sentence version

A cloud LLM decides what to do; a lean, always-on local process does it,
behind a permission gate that the model cannot talk its way past.

## The shape

```
  You, in a terminal
        │  text
        ▼
  FridayAssistant (app/core/assistant.py)
        │   builds a small, cache-friendly prompt (context engine)
        │   runs an agentic tool loop, max 6 rounds
        ▼
  LLM provider (app/llm/provider.py)  ──►  Groq / OpenAI / Gemini / Anthropic
        │   returns text and/or tool calls (neutral LLMReply)
        ▼
  toolkit.execute() (app/core/toolkit.py)   ← THE choke point
        │   classifies each call by risk tier (app/core/tiers.py)
        ├── AUTO     → run now, audit-log it
        ├── CONFIRM  → pause, ask you; run only on "yes"
        └── NEVER    → refuse, always
        ▼
  tools ──► SQLite (memory, tasks, emails, …)  via MemoryManager
        └─► native_bridge ──► friday_watcher (Rust)  over a Unix socket
                                   system stats + inotify file events
```

Everything the model learns about your machine, your data, or the current
time comes back *through a tool result*. The prompt forbids inventing facts,
but the real guarantee is that the model has no other source.

## The pieces, and why they're built this way

### Orchestrator — `app/core/assistant.py`
The conversational core. One method, `chat(conversation_id, message)`, does:
1. Slash commands (`/help`, `/cost`, `/provider`, …) run locally, free, and
   never touch the LLM. Checked first — but guarded so an approval word like
   `/approve` still reaches a pending proposal.
2. Otherwise it builds the prompt (see context engine) and runs the **tool
   loop**: call the model → if it asked for tools, execute them → feed
   results back → repeat, up to `MAX_TOOL_ROUNDS = 6`. The bound is a
   safety/cost stop, not an expected limit.
3. If a tool needs approval, the loop **pauses**: it stashes a
   `PendingApproval` (the in-progress message list + held calls) keyed by
   conversation and returns a proposal. The next message resolves it.

Why a loop and not a keyword router: the original design routed intent
through a ~200-line if/elif chain. It was a quality ceiling — it could only
do what someone had hand-wired. Native tool calling lets the model compose
tools it was never explicitly told to combine.

Pending approvals are **in-memory only**. A restart drops them, which fails
safe: nothing runs without a fresh proposal.

### Context engine — `app/core/context.py`
Decides what goes into each call, under two pressures: token cost (cloud
brain) and cache reuse.
- **Rolling summary.** Old turns are folded into a running summary
  (`Conversation.summary`) once `SUMMARY_TRIGGER` messages accumulate,
  keeping the last `RECENT_WINDOW` verbatim. Folding happens in a background
  task after a reply, so it never blocks you.
- **Selective memory recall.** Instead of injecting every stored fact,
  `select_memories()` scores facts by keyword overlap with your current
  message (recency breaks ties; pure recency is the fallback for greetings),
  capped at `MEMORY_LIMIT`.
- **Cache-friendly layout.** The system prompt is byte-identical every call
  so provider-side prompt caching can reuse it; the volatile stuff (summary,
  recalled memories) rides in a *second* system message after it.

### Permission tiers — `app/core/tiers.py` + the `toolkit.execute()` gate
The trust feature. Fully documented in `docs/PERMISSIONS.md`. The short
version: every tool has an explicit tier; enforcement is in code at the
executor, not in the prompt; deletion has no tool. See ADR-007 for why.

### Tools — `app/core/toolkit.py`, `app/core/tools.py`
`toolkit.py` is the registry: each tool is a function plus a JSON-schema
spec handed to the LLM. `execute()` is the gate — it classifies, checks
approval, filters arguments to the schema, runs the handler, and audit-logs
the decision (`AUDIT tier=N tool=… decision=…`). `tools.py` holds the actual
readers (system info, DB summaries), each of which prefers the native daemon
and falls back to pure Python.

### Provider abstraction — `app/llm/`
- `types.py` — the neutral contract: `ToolSpec`, `ToolCall`, `LLMReply`,
  `Usage`. The orchestrator only ever sees these.
- `provider.py` — pure converter functions (`to_anthropic_payload`,
  `to_openai_messages`, `to_gemini_payload`, …) plus one class per provider.
  Converters are separated from network calls so they're unit-testable
  without a key. `raw_content` replays provider-native blocks (Claude
  thinking, Gemini functionCall parts) verbatim across tool-loop turns,
  which some providers require.
- `costs.py` — every response's token `Usage` is logged per call and summed
  per session (`/cost`). Totals are in-memory; they reset with the process.

Why the seam: it makes the brain swappable — including, eventually, a local
model — without the orchestrator knowing. See ADR-009.

### Local body — `native/watcher/` (Rust) + `app/core/native_bridge.py`
`friday_watcher` is a threaded Rust daemon on `/tmp/friday_watcher.sock`. It
serves system stats (SYSINFO, PROCS, HEALTH, BATTERY, NETWORK, TEMPS) with
the same output format as the legacy C monitor, plus file watching (WATCH,
UNWATCH, WATCHES, EVENTS). ~2.9MB RSS. See ADR-008.

`native_bridge` is the Python client, with a three-step fallback per query:
Rust watcher → legacy C monitor (`native/friday_monitor.c`) → pure-Python
`/proc` reads. The chat path never crashes because a daemon is down; it
degrades. (Caveat: the pure-Python leg is under-tested — see the project
retrospective.)

### Persistence — `app/memory/`
SQLAlchemy over SQLite (WAL mode, so the terminal and a `--headless` service
can share the file). `MemoryManager` is the CRUD layer. `models.py` also
contains the frozen Phase 2.8–3.3 substrate tables — present, not used by the
chat path. Schema changes to existing tables go through the additive
`_migrate()` in `database.py` (create_all never alters existing tables).

### Scheduler — `app/scheduler/service.py`
APScheduler background jobs: email sync, health checks, deadline alerts.
Runs in both terminal and `--headless` modes.

## What is deliberately NOT wired in
Phases 2.8–3.3 built an actor system, event bus with event sourcing, MQTT
transport, circuit breakers, a workflow engine, and device abstraction —
~139 tests, all passing. **None of it is imported by the chat path.** It is
frozen (ADR-006): kept and tested in place, not extended, until a real
feature needs exactly that machinery. Don't build new features on it without
revisiting that decision.

## Entry points — `main.py`
- `python main.py` — terminal chat (default). No Qt imported.
- `python main.py --ui` — Qt dashboard; PySide6 loads only here.
- `python main.py --headless` — body only (scheduler + watcher, no chat),
  for the systemd service. SIGTERM shuts down cleanly.

See `docs/SETUP.md` for running it.
