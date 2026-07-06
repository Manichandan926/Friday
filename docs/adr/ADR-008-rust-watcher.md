# ADR 008: Rust for the Watcher Daemon, Not Extending the C Monitor

## Status
Accepted (2026-07-06)

## Context
FRIDAY needs an always-on local "body": a small daemon that reads system
stats and watches the filesystem, serving a Python orchestrator over a Unix
socket. A C monitor (`native/friday_monitor.c`) already existed and served
stats well. Milestone 4 needed to add inotify file watching. Two paths:
extend the C daemon, or rewrite in Rust.

The user's stated priority when this was scoped: "safety and speed must and
should." On an 8GB machine, the daemon must also stay tiny.

## Decision
Rewrite as `native/watcher/` in Rust (`friday_watcher`).

Reasons:
- **Memory safety in an always-on process.** The C monitor does manual
  buffer management (`snprintf` offset juggling, fixed `char[]` caches)
  across a threaded socket server. Adding inotify — variable-length event
  streams, watch-descriptor bookkeeping — multiplies the room for a
  buffer overflow or use-after-free in a process that runs continuously.
  Rust removes that entire class of bug at compile time.
- **The speed argument for C didn't hold.** Both are `/proc` readers gated
  by a 2-second cache; the bottleneck is I/O, not language. Measured RSS:
  ~2.9MB for the Rust daemon, well under the 10MB budget and comparable to
  the C one. The 454K binary is not a concern.
- **inotify is materially safer to express in Rust** via the `nix` crate's
  typed wrappers than as raw `read()` loops over the inotify fd in C.

Design constraints kept it lean: std threads only (no async runtime), size-
optimized release profile (`opt-level="s"`, LTO, `panic="abort"`, stripped).

## Consequences
- The build now needs a Rust toolchain (user-level rustup, minimal profile).
  Contributors without it can still run everything — the daemon is optional
  and the bridge falls back.
- **The C monitor is kept, not deleted.** `native_bridge` tries the Rust
  socket first, then the C socket, then pure-Python readers. The C daemon
  is the proven fallback until the Rust one has run trouble-free long enough
  to retire it. Output format parity between the two was verified command by
  command so the Python side needs no branching.
- Two native daemons temporarily coexist. That's deliberate redundancy
  during the transition, not permanent architecture.
