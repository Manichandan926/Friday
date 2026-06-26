# ADR 002: Actor Model

## Status
Accepted

## Context
Handling hundreds of concurrent MQTT streams, UI interactions, LLM inferences, and database writes using standard `threading.Lock` and mixed `asyncio` leads to frequent deadlocks, resource exhaustion on low-end hardware, and massive code complexity.

## Decision
We adopt the Actor Model for concurrency. All components that manage state or external I/O (e.g. database writing, MQTT, external sensors) must be wrapped in an `Actor`. Actors communicate strictly through asynchronous message passing (mailboxes) and share zero state. 

## Consequences
- **Pros:** Total elimination of lock contention; natural fit for eventual Tokio/Rust migration; built-in fault tolerance (Supervisor/Let It Crash).
- **Cons:** Forces asynchronous patterns on everything; debugging requires distributed tracing techniques rather than simple stack traces.
