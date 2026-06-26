# ADR 001: Event Sourcing

## Status
Accepted

## Context
FRIDAY is transitioning from a simple desktop assistant to a distributed IoT and AI operating system kernel. As the system scales, multiple components (UI, automation rules, remote clients) need to know the true state of the system. Mutating state directly causes race conditions, debugging nightmares, and makes it impossible to synchronize external nodes.

## Decision
We will use Event Sourcing. All state changes must occur as immutable `Event` objects published to the `EventBus`. The `StateStore` acts as a materialized view (projection) by listening to the event stream and applying reducers.

## Consequences
- **Pros:** Full audit trail, deterministic state reconstruction, easy synchronization with mobile/remote clients via event replay, native CQRS support.
- **Cons:** Slight overhead parsing and serializing events; state changes are eventually consistent (though heavily optimized via local event loops).
