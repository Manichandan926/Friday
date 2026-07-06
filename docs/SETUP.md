# FRIDAY Setup & Running

Assumes you've never seen this repo. Linux, Python 3.12+. Tested on Fedora
KDE, i3 / 8GB / no GPU.

## 1. Python environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configuration (`.env`)

```bash
cp .env.example .env
```

Then edit `.env`. FRIDAY reads it via `app/core/config.py`. The settings
that matter:

| Variable | What it does | Default if unset |
|----------|--------------|------------------|
| `DEFAULT_LLM_PROVIDER` | Which brain to use | `groq` |
| `DEFAULT_LLM_MODEL` | Model for Groq/OpenAI | `llama-3.1-8b-instant` |
| `GROQ_API_KEY` | Key for Groq (free tier available) | empty |
| `OPENAI_API_KEY` | Key for OpenAI | empty |
| `GEMINI_API_KEY` | Key for Gemini | empty |
| `ANTHROPIC_API_KEY` | Key for Claude | empty |
| `ANTHROPIC_MODEL` | Model when provider is claude | `claude-opus-4-8` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

**You only need the key for the provider you use.** With the default
(`groq`), set `GROQ_API_KEY` and you're done — get a free key at
console.groq.com.

> ⚠️ **Change `DEFAULT_LLM_MODEL`.** The code default,
> `llama-3.1-8b-instant`, fabricates answers instead of calling tools — it
> will confidently make up your task list. Set:
> ```
> DEFAULT_LLM_MODEL=llama-3.3-70b-versatile
> ```
> Rule of thumb: keep the default at ~70B-class or above while tool calling
> is in the chat path (ADR-009). `.env.example` should already reflect this;
> confirm it does.

**If a key is missing:** FRIDAY doesn't crash. The provider factory raises
`ProviderNotConfigured` with a message naming the exact variable to set, and
the chat returns that message instead of a reply. Switch providers live with
`/provider <name>`; if that provider's key is missing you get the same clear
message.

Secrets (`.env`, `credentials.json`, `token.json`) are chmod-600'd on
startup and are gitignored — keep it that way.

## 3. Native watcher (optional but recommended)

Gives you instant system stats (~3MB RSS) and file watching. Without it,
FRIDAY falls back to pure-Python `/proc` reads — slower, but everything
still works.

Needs a Rust toolchain. If you don't have one (user-level, no sudo):
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- \
    -y --profile minimal --no-modify-path
```

Build it:
```bash
cd native/watcher
~/.cargo/bin/cargo build --release
cd ../..
```

`main.py` starts the daemon automatically when present, preferring the Rust
watcher, then the legacy C monitor (`native/friday_monitor.c`, built with
`make` in `native/`), then the Python fallback.

## 4. Run it

```bash
python main.py            # terminal chat — the default
python main.py --ui       # Qt desktop dashboard (loads PySide6 on demand)
python main.py --headless # body only: scheduler + watcher, no chat loop
```

`--no-ui` is accepted as a legacy alias but does nothing special — the
terminal is already the default.

In the chat, just talk. "how much RAM am I using?", "watch my Downloads
folder", "remember that I prefer Rust". Slash commands (`/help`, `/cost`,
`/brief`, `/provider`, …) are free shortcuts that skip the LLM — `/help`
lists them.

## 5. Run on boot (systemd user service)

The service runs `--headless` — the always-on body, no chat loop. Chat from
any terminal whenever you want; SQLite WAL lets both share the database.

```bash
cp friday.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now friday.service
systemctl --user status friday.service
```

## 6. Tests

```bash
python -m pytest -q
```

The watcher tests spawn a private daemon on their own socket and **skip**
cleanly if you haven't built the release binary — so a green run without
Rust is expected, it just won't have exercised the Rust daemon.

## Troubleshooting

- **"No API key configured for 'groq'…"** — set `GROQ_API_KEY` in `.env` and
  restart, or `/provider <name>` to one you have a key for.
- **FRIDAY makes things up / doesn't check the system** — you're on the 8B
  model. Set `DEFAULT_LLM_MODEL=llama-3.3-70b-versatile` (§2).
- **Stats feel slow** — the native watcher isn't built or isn't running
  (§3); you're on the Python fallback. Check `logs/friday.log` for which
  daemon connected.
- **Groq `tool_use_failed` 400 in the logs** — Groq's Llama occasionally
  emits malformed tool-call syntax. FRIDAY auto-retries without tools so the
  turn still answers; it just answers without tool data that once. Provider-
  side flakiness, not a bug you can fix here.
