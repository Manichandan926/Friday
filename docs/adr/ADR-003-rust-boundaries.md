# ADR 003: Rust Boundaries (PyO3)

## Status
Accepted

## Context
Python is excellent for orchestration, dynamic business logic, and UI (PySide6), but it struggles with high-throughput stream processing, complex real-time search indexing, and low-latency hardware event loops. Completely rewriting FRIDAY in Rust is a classic "Second System Effect" trap.

## Decision
We adopt a polyglot architecture: "Python Brain, Rust Muscles". Rust will be used strictly for performance-critical engines (EventEngine, SearchEngine, Vector/Stream processing). These engines will be compiled via PyO3 and exposed as Python classes. The boundary will be rigorously enforced using `.pyi` interface contracts (e.g., `EventRuntime`, `ActorRuntime`).

## Consequences
- **Pros:** Best of both worlds (Python's ecosystem and speed of development + Rust's performance and safety). Prevents the need to rewrite automation rules or UI.
- **Cons:** Increased build complexity; FFI boundary serialization overhead must be managed carefully.
