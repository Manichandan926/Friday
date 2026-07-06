# FRIDAY — working notes for agents

A persistent personal AI assistant that runs on the owner's Linux laptop:
a cloud LLM decides what to do, a lean always-on local process does it, behind
a permission gate the model cannot talk its way past. Phase 1 (milestones 1–5)
is complete.

This file is the project map. `AGENTS.md` is the working *style* (read it —
lazy-senior-dev: reuse before you write, smallest correct diff, one runnable
check behind non-trivial logic). The deep docs live in `docs/`.

## The one thing to internalize

**Everything the model can do is a tool, and every tool has a risk tier
enforced in code at one choke point — `toolkit.execute()`, not the prompt.**
`AUTO` runs now, `CONFIRM` waits for the user's "yes", `NEVER` is refused
always. A confused or swapped model still can't cross a tier. If you touch the
tool surface, you are touching the trust model — see `docs/PERMISSIONS.md`.

## Run & test

The project venv is at `.venv/`. **Always use its interpreter** — a bare
`python`/`pytest` will miss deps (`anthropic`, etc.):

```bash
.venv/bin/python -m pytest -q            # full suite (should be all green)
.venv/bin/python main.py                 # terminal chat (default; no Qt loaded)
.venv/bin/python main.py --ui            # Qt dashboard (PySide6 loads only here)
.venv/bin/python main.py --headless      # body only (scheduler + watcher), for systemd
```

Needs `GROQ_API_KEY` (default provider) in `.env` — copy `.env.example`.
Switch providers live with `/provider {groq|openai|gemini|claude}`. The tools
and the whole test suite run with **no** LLM calls (tests use a FakeProvider),
so Groq being rate-limited never blocks development or testing.

## Map

| Area | File | Note |
|------|------|------|
| Orchestrator / tool loop / approval flow | `app/core/assistant.py` | `chat()`, max 6 tool rounds, in-memory pending approvals |
| Context engine | `app/core/context.py` | rolling summary, selective memory recall, cache-friendly prompt |
| Risk tiers | `app/core/tiers.py` | `TOOL_TIERS` map + dynamic `classify_command` |
| Tool registry + the gate | `app/core/toolkit.py` | `_register(...)` specs, `execute()` enforces tiers, audit-logs |
| Data readers (native → /proc fallback) | `app/core/tools.py` | |
| Desktop control | `app/core/desktop.py` | media/volume/brightness/notify/clipboard/screenshot/open/play/calc; subprocess argv, `shell=False` |
| Shell safety | `app/core/shell.py` | whitelist + `has_command_substitution` (substitution = NEVER) |
| Providers | `app/llm/` | neutral `LLMReply`/`ToolSpec`; `costs.py` persists usage to SQLite |
| Local body (Rust) | `native/watcher/` + `app/core/native_bridge.py` | Unix socket; 3-tier fallback Rust → C → Python |
| Persistence | `app/memory/` | SQLAlchemy/SQLite (WAL); additive `_migrate()` in `database.py` |
| Scheduler | `app/scheduler/service.py` | APScheduler; fires `notify-send` when a task's due_date passes |

## Adding a tool (the one workflow you'll repeat)

1. `@_register(name, description, params, required)` in `toolkit.py` — the
   description is what the model sees; be precise about side effects.
2. Add the tool to `TOOL_TIERS` in `tiers.py`. **A build-time guardrail
   (`test_tiers.py::test_every_registered_tool_has_an_explicit_tier`) fails if
   you don't** — you cannot ship an untiered tool by accident.
3. Confine side effects at the trust boundary: resolve and bound paths (see
   `_safe_path` / `ALLOWED_WRITE_ROOTS`), don't trust the argument. Shell out
   only via argv lists with `shell=False`.
4. Write an **adversarial** test, not just happy-path — prove it refuses what
   it should. Match the mocked-CLI style in `tests/test_desktop.py`.

## Load-bearing invariants (don't break these unknowingly)

- **Tiers are enforced in `execute()`, and fences live inside the tool** — so a
  CONFIRM fence (e.g. home-only) still holds even when approval is granted.
- **The frozen substrate is not wired in.** Phases 2.8–3.3 (actor system,
  event bus, MQTT, workflow engine, ~139 tests) is kept and tested but
  imported by nothing on the chat path. Don't build on it without revisiting
  ADR-006.
- **The scheduler compares due dates against naive UTC now** — store reminder
  due dates the same way (`datetime.now(timezone.utc).replace(tzinfo=None)`).
- **Lean is a permanent constraint** (8GB RAM, no GPU): avoid heavyweight deps;
  the Rust watcher exists to stay ~3MB RSS. Prefer already-installed CLIs.
- **Desktop is GNOME/Wayland** (not KDE): X11 window tools (`xdotool`,
  `wmctrl`) don't work; screenshots need `gnome-screenshot`; volume is `wpctl`.

## Deeper reading

`docs/ARCHITECTURE.md` (system shape + why), `docs/PERMISSIONS.md` (tier model,
approval flow, add-a-tool, known gaps), `docs/SETUP.md` (install/run),
`docs/adr/` (decision records — ADR-006 freeze, ADR-007 tiers-at-executor,
ADR-008 Rust watcher, ADR-009 cloud-brain/local-body).
