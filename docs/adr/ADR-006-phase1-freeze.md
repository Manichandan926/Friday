# ADR 006: Phase 2.8–3.3 Infrastructure Freeze

## Status
Accepted (2026-07-06)

## Context
Phases 2.8–3.3 built a large infrastructure substrate: actor system,
event bus with event sourcing, MQTT transport, circuit breakers, state
store, workflow engine, device abstraction, and policy engine. It is
well-tested (139 tests) but none of it is wired into the chat path —
the part of FRIDAY that gets daily use. Meanwhile the assistant itself
(tool loop, tier permissions, context engine, native watcher) was the
actual bottleneck and has now been built directly.

## Decision
The substrate is **frozen**: not deleted, not extended, not wired into
new features. The chat path (`assistant.py`, `toolkit.py`, `tiers.py`,
`context.py`, `app/llm/*`, `native_bridge.py`) must not import from
`app/core/actors`, `app/core/events`, or the MQTT/workflow/device
modules. Its tests keep running so the code stays healthy in place.

The freeze lifts only when a real feature needs exactly this machinery
and the Phase-1 assistant is already daily-usable.

## Consequences
- New features build on the thin chat path, keeping idle RSS low
  (8GB-laptop constraint is permanent).
- Superseded pre-milestone specs (FRIDAY_MASTER_SPEC.md,
  architecture.md, manual.md, FRIDAY_ANALYSIS_AND_OPTIMIZATION.md)
  were removed in the same change; they described the frozen direction
  and live on in git history.
