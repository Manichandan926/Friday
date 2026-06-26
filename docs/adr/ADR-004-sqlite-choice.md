# ADR 004: SQLite Choice & Batching

## Status
Accepted

## Context
FRIDAY runs on resource-constrained hardware (e.g. Intel i3). Running a full PostgreSQL cluster is overkill and wastes precious RAM that should be reserved for the LLM context. However, standard SQLite struggles with high concurrent writes.

## Decision
We will use SQLite as the primary datastore, heavily tuned:
- `WAL` (Write-Ahead Logging) enabled.
- `NORMAL` synchronous mode.
- In-memory temp store and memory mapping.
Furthermore, all database writes go through a single `DBWriterActor` that batches writes in memory (e.g., 100 records or 500ms intervals) before executing a single transaction.

## Consequences
- **Pros:** Zero-configuration deployment; minimal RAM usage; massive I/O reduction via actor batching; perfectly capable of handling millions of rows if indexed properly.
- **Cons:** Cannot easily scale horizontally across multiple compute nodes without a network overlay like Litestream or Turso (acceptable limitation for a local-first OS).
