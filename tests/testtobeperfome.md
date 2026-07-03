# FRIDAY PAIOS — Complete Test Plan

> **Status:** Phase 3.3 — Architecture Stabilization  
> **Hardware:** Intel i3-1005G1, 8GB RAM, No GPU  
> **Philosophy:** Ponytail — test what matters, skip what doesn't

---

## How to Run

All tests run from the project root with:

```bash
cd ~/Projects/FRIDAY
PYTHONPATH=. .venv/bin/python -m pytest tests/ -v
```

For a single file:

```bash
PYTHONPATH=. .venv/bin/python tests/<test_file>.py
```

---

## 1. Actor System Tests

**File:** `tests/test_actor_system.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 1.1 | `test_actor_spawn_and_start` | Actor spawns, transitions to running, processes a message |
| 1.2 | `test_actor_stop` | Actor stops cleanly, drains mailbox |
| 1.3 | `test_actor_message_ordering` | Messages processed in FIFO order |
| 1.4 | `test_actor_mailbox_depth_metric` | `actor_mailbox_depth` gauge recorded in MetricsRegistry |
| 1.5 | `test_actor_processing_time_metric` | `actor_processing_time` histogram records latency |
| 1.6 | `test_actor_system_send_to_missing` | Sending to nonexistent actor returns False, increments `actor_dropped_messages` |
| 1.7 | `test_actor_system_start_all_stop_all` | All actors start/stop in one call |
| 1.8 | `test_actor_hot_spawn` | Spawning an actor while system is running auto-starts it |

---

## 2. Supervisor & Fault Tolerance Tests

**File:** `tests/test_supervisor.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 2.1 | `test_supervisor_catches_crash` | Supervisor receives `ACTOR_CRASHED` when a watched actor throws |
| 2.2 | `test_supervisor_restarts_actor` | Actor is restarted after crash with exponential backoff |
| 2.3 | `test_supervisor_restart_counter` | `actor_restarts` counter increments per crash |
| 2.4 | `test_supervisor_max_restarts` | After N restarts in short window, supervisor stops retrying (circuit opens) |
| 2.5 | `test_supervisor_watches_multiple` | Supervisor can watch and restart multiple independent actors |

---

## 3. EventBus Tests

**File:** `tests/test_event_bus.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 3.1 | `test_publish_and_subscribe` | Publish an event, subscriber callback fires |
| 3.2 | `test_priority_ordering` | CRITICAL events dispatched before LOW events |
| 3.3 | `test_backpressure_low_queue_drops_oldest` | When LOW queue exceeds 10,000, oldest event is dropped |
| 3.4 | `test_backpressure_medium_queue_drops_oldest` | Same for MEDIUM queue |
| 3.5 | `test_backpressure_high_queue_rejects` | HIGH queue full → publish returns rejection |
| 3.6 | `test_ring_buffer_cache` | `get_recent_events()` returns last N events from deque without hitting DB |
| 3.7 | `test_ring_buffer_capacity` | Ring buffer never exceeds 10,000 entries |
| 3.8 | `test_wildcard_subscription` | Subscribing to `device.*` receives `device.telemetry` and `device.command` |
| 3.9 | `test_middleware_chain` | MetricsMiddleware increments counters on publish |
| 3.10 | `test_event_bus_start_stop` | Clean start/stop lifecycle, no dangling tasks |

---

## 4. Event Sourcing & State Store Tests

**File:** `tests/test_state_store.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 4.1 | `test_initial_state` | Default state tree contains `system`, `counters`, `devices` keys |
| 4.2 | `test_reducer_updates_state` | Registering a reducer and dispatching an event mutates state correctly |
| 4.3 | `test_state_immutability` | `get_state()` returns a deep copy, external mutation does not corrupt store |
| 4.4 | `test_selector` | Selector extracts a specific slice (e.g., `system.health.cpu_pct`) |
| 4.5 | `test_listener_fires_on_change` | State listener callback receives `(old_state, new_state)` on change |
| 4.6 | `test_event_bus_binding` | StateStore bound to EventBus, events auto-reduce into state |

---

## 5. Snapshot Manager Tests

**File:** `tests/test_snapshot_manager.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 5.1 | `test_snapshot_creates_record` | Snapshot manager persists current state to DBWriterActor |
| 5.2 | `test_snapshot_replay` | State can be reconstructed from a snapshot + replayed events |
| 5.3 | `test_ttl_pruning` | Old events beyond TTL threshold are pruned after snapshot |

---

## 6. DBWriterActor Tests

**File:** `tests/test_db_writer.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 6.1 | `test_single_write` | Sending a WRITE_AUDIT message results in a row in `audit_records` |
| 6.2 | `test_batch_flush_on_count` | 100 buffered writes trigger automatic flush |
| 6.3 | `test_batch_flush_on_timeout` | Writes buffered < 100 still flush after 500ms |
| 6.4 | `test_flush_on_stop` | Remaining buffer is flushed when actor stops |
| 6.5 | `test_event_store_write` | `WRITE_EVENT` messages persist to `event_store` table |
| 6.6 | `test_snapshot_write` | `WRITE_SNAPSHOT` messages persist to `state_snapshots` table |
| 6.7 | `test_delete_old_events` | `DELETE_OLD_EVENTS` purges events older than threshold |

---

## 7. DeadLetterActor Tests

**File:** `tests/test_dead_letter.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 7.1 | `test_capture_failed_message` | Malformed message lands in dead letter queue |
| 7.2 | `test_failure_metadata` | Captured failure contains `error_type`, `source`, `timestamp`, `raw_data` |
| 7.3 | `test_get_failures_returns_list` | `get_failures()` returns all captured failures |
| 7.4 | `test_max_retention` | Dead letter queue does not grow unbounded |

---

## 8. Transport Layer Tests

**File:** `tests/test_transports.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 8.1 | `test_transport_message_dataclass` | `TransportMessage` holds `topic`, `payload`, `qos` |
| 8.2 | `test_mqtt_transport_connect_disconnect` | MQTTTransport lifecycle (mocked broker) |
| 8.3 | `test_mqtt_transport_publish` | Publish a message through fake transport |
| 8.4 | `test_mqtt_transport_subscribe_callback` | Subscribe fires callback on incoming message |

---

## 9. MQTTActor Tests

**File:** `tests/test_mqtt_actor.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 9.1 | `test_inbound_mqtt_to_event` | MQTT telemetry payload → parsed → published as FRIDAY Event |
| 9.2 | `test_outbound_event_to_mqtt` | `device.command` event → routed → published to MQTT broker |
| 9.3 | `test_malformed_mqtt_to_dead_letter` | Bad JSON payload → forwarded to DeadLetterActor |
| 9.4 | `test_topic_parsing` | `friday/devices/light_01/status` correctly extracts device_id |

---

## 10. Workflow Engine Tests

**File:** `tests/test_workflow_engine.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 10.1 | `test_register_workflow` | Workflow registers and is indexed by trigger event type |
| 10.2 | `test_trigger_fires_actions` | Matching event triggers workflow actions |
| 10.3 | `test_condition_blocks_action` | Unmet condition prevents action execution |
| 10.4 | `test_time_condition` | TimeCondition evaluates after/before correctly |
| 10.5 | `test_state_condition` | StateCondition checks StateStore for expected value |
| 10.6 | `test_multiple_workflows_same_trigger` | Multiple workflows on same event type all evaluate |

---

## 11. Circuit Breaker Tests

**File:** `tests/test_circuit_breaker.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 11.1 | `test_closed_state_allows_calls` | Default CLOSED state lets calls through |
| 11.2 | `test_failure_threshold_opens_circuit` | 5 consecutive failures → state transitions to OPEN |
| 11.3 | `test_open_state_raises_exception` | Calls during OPEN state raise `CircuitBreakerOpenException` |
| 11.4 | `test_recovery_timeout_to_half_open` | After timeout elapses, state transitions to HALF_OPEN |
| 11.5 | `test_half_open_success_closes` | Successful call in HALF_OPEN → back to CLOSED |
| 11.6 | `test_half_open_failure_reopens` | Failed call in HALF_OPEN → back to OPEN |

---

## 12. Rate Limiter Tests

**File:** `tests/test_rate_limiter.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 12.1 | `test_create_bucket` | Bucket created with correct capacity and refill rate |
| 12.2 | `test_acquire_within_limit` | Acquiring tokens within capacity succeeds |
| 12.3 | `test_acquire_exceeds_limit` | Acquiring more tokens than available returns False |
| 12.4 | `test_token_refill` | After waiting, tokens refill at the configured rate |
| 12.5 | `test_missing_bucket_allows` | Acquiring from nonexistent bucket returns True (safe default) |

---

## 13. Metrics System Tests

**File:** `tests/test_metrics.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 13.1 | `test_counter_increment` | Counter increments and `get_current()` returns correct value |
| 13.2 | `test_counter_negative_rejected` | Negative increment raises ValueError |
| 13.3 | `test_gauge_set` | Gauge records latest value |
| 13.4 | `test_histogram_observe` | Histogram records value and sorts into buckets |
| 13.5 | `test_export_json` | `export_json()` returns all metrics as serializable dict |
| 13.6 | `test_registry_singleton` | `get_instance()` returns same object |
| 13.7 | `test_history_bounded` | Value history never exceeds 1000 entries |

---

## 14. Shell Executor & Safety Tests

**File:** `tests/test_shell.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 14.1 | `test_safe_command_allowed` | `ls`, `ps`, `df`, `pwd` pass safety check |
| 14.2 | `test_dangerous_command_blocked` | `rm`, `sudo`, `kill`, `reboot` blocked |
| 14.3 | `test_pipe_chain_validation` | `ps aux \| grep python \| head -5` allowed |
| 14.4 | `test_pipe_with_dangerous_blocked` | `ls \| rm -rf` blocked |
| 14.5 | `test_fork_bomb_blocked` | `:(){ :\|:& };:` blocked |
| 14.6 | `test_execution_timeout` | Command exceeding 5s is killed |
| 14.7 | `test_output_truncation` | Output > 3000 chars is truncated |
| 14.8 | `test_empty_command_rejected` | Empty string returns `(False, reason)` |

---

## 15. Ponytail Skill Integration Tests

**File:** `tests/test_ponytail.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 15.1 | `test_inject_full_mode` | `inject_ponytail(prompt, "full")` prepends PONYTAIL RULES + FULL level |
| 15.2 | `test_inject_lite_mode` | Lite mode injects correct level tag |
| 15.3 | `test_inject_ultra_mode` | Ultra mode injects "YAGNI extremist" tag |
| 15.4 | `test_inject_off_mode` | Off mode returns original prompt unchanged |
| 15.5 | `test_all_agents_import_ponytail` | All 7 agents import and use `inject_ponytail` |

---

## 16. Agent LLM Tests (Mocked Provider)

**File:** `tests/test_agents.py`

> These tests mock the LLM provider to avoid real API calls.

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 16.1 | `test_shell_agent_generates_command` | ShellAgent returns valid JSON with `command` key |
| 16.2 | `test_shell_agent_blocks_unsafe` | ShellAgent does not execute blocked commands |
| 16.3 | `test_task_agent_parses_tasks` | TaskAgent returns structured task list from LLM JSON |
| 16.4 | `test_memory_agent_extracts_facts` | MemoryAgent extracts user-stated facts, skips system data |
| 16.5 | `test_memory_agent_deduplication` | MemoryAgent skips facts that already exist |
| 16.6 | `test_inbox_agent_returns_metadata` | InboxAgent returns `summary`, `category`, `priority` |
| 16.7 | `test_placement_agent_tracks_application` | PlacementAgent creates application record in DB |
| 16.8 | `test_planner_agent_generates_briefing` | PlannerAgent returns non-empty markdown briefing |

---

## 17. Database & Memory Tests

**File:** `tests/test_database.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 17.1 | `test_init_db_creates_tables` | `init_db()` creates all model tables |
| 17.2 | `test_add_and_get_conversation` | Create conversation, retrieve by ID |
| 17.3 | `test_add_and_get_messages` | Add messages to conversation, retrieve in order |
| 17.4 | `test_add_and_get_tasks` | CRUD for tasks with priority and due_date |
| 17.5 | `test_add_and_get_memory_items` | CRUD for memory items |
| 17.6 | `test_add_and_get_knowledge` | CRUD for knowledge vault items |
| 17.7 | `test_search_knowledge` | FTS search returns matching knowledge entries |
| 17.8 | `test_add_and_get_emails` | CRUD for emails |
| 17.9 | `test_add_and_get_applications` | CRUD for placement applications |
| 17.10 | `test_notifications` | Create, read, mark-as-read for notifications |

---

## 18. Service Registry Tests

**File:** `tests/test_service_registry.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 18.1 | `test_register_and_resolve` | Register a service, resolve it by type |
| 18.2 | `test_resolve_missing_raises` | Resolving unregistered type raises |
| 18.3 | `test_singleton_behavior` | Registry is a singleton |

---

## 19. Runtime Interface Stub Tests

**File:** `tests/test_runtime_interfaces.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 19.1 | `test_event_runtime_pyi_exists` | `.pyi` file exists and has `publish`, `subscribe`, `start`, `stop` |
| 19.2 | `test_actor_runtime_pyi_exists` | `.pyi` file exists and has `spawn`, `send`, `start_all`, `stop_all` |
| 19.3 | `test_transport_runtime_pyi_exists` | `.pyi` file exists and has `connect`, `disconnect`, `publish`, `subscribe` |

---

## 20. Chaos & Stress Tests

**File:** `tests/chaos/actor_crashes.py` (already exists)

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 20.1 | `test_actor_survives_random_crashes` | FragileActor processes work despite 10% crash rate |
| 20.2 | `test_supervisor_recovery_under_load` | Supervisor restarts actors while system handles 100 messages |

**File:** `tests/chaos/queue_overflow.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 20.3 | `test_event_bus_survives_100k_flood` | 100,000 events published without OOM or deadlock |
| 20.4 | `test_backpressure_drops_not_crashes` | Queue overflow drops oldest events, never raises |

**File:** `tests/chaos/memory_pressure.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 20.5 | `test_ring_buffer_bounded_under_flood` | Ring buffer stays at 10k entries under sustained 100k event flood |
| 20.6 | `test_metrics_history_bounded` | Metric value lists stay ≤ 1000 entries |

---

## 21. Benchmark Tests

**File:** `benchmarks/event_bus_bench.py` (already exists)

| # | Benchmark | Target |
|---|-----------|--------|
| 21.1 | EventBus Publish Rate | > 10,000 events/sec |
| 21.2 | EventBus Dispatch Rate | > 5,000 events/sec |

**File:** `benchmarks/actor_bench.py` (already exists)

| # | Benchmark | Target |
|---|-----------|--------|
| 21.3 | Actor Enqueue Rate | > 50,000 msg/sec |
| 21.4 | Actor Receive Rate | > 20,000 msg/sec |

**File:** `benchmarks/sqlite_bench.py` (to be created)

| # | Benchmark | Target |
|---|-----------|--------|
| 21.5 | DBWriterActor Batch Write | > 1,000 rows/sec |
| 21.6 | Event Query by Type | < 10ms for 100k rows |

---

## 22. Integration Tests

**File:** `tests/test_integration.py`

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 22.1 | `test_event_to_state_store` | Publish event → reducer fires → StateStore updated |
| 22.2 | `test_event_to_workflow` | Publish event → WorkflowEngine evaluates → action fires |
| 22.3 | `test_event_to_snapshot` | Events accumulate → SnapshotManager persists → State reconstructible |
| 22.4 | `test_mqtt_inbound_to_workflow` | MQTT message → MQTTActor → EventBus → WorkflowEngine action |
| 22.5 | `test_full_actor_lifecycle` | Spawn → Start → Process → Crash → Supervisor Restart → Process again |

---

## Test Priority

```text
MUST PASS (blocking):
  - Actor System (1.x)
  - Supervisor (2.x)
  - EventBus (3.x)
  - Shell Safety (14.x)
  - Database CRUD (17.x)

SHOULD PASS (important):
  - State Store (4.x)
  - DBWriter (6.x)
  - DeadLetter (7.x)
  - Circuit Breaker (11.x)
  - Rate Limiter (12.x)
  - Metrics (13.x)

NICE TO HAVE (stability):
  - Chaos Tests (20.x)
  - Benchmarks (21.x)
  - Integration (22.x)
```

---

## Running the Full Suite

```bash
# Unit tests
PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --ignore=tests/chaos

# Chaos tests (expect stack traces — that's the point)
PYTHONPATH=. .venv/bin/python tests/chaos/actor_crashes.py

# Benchmarks
PYTHONPATH=. .venv/bin/python benchmarks/event_bus_bench.py
PYTHONPATH=. .venv/bin/python benchmarks/actor_bench.py

# Single test file
PYTHONPATH=. .venv/bin/python -m pytest tests/test_circuit_breaker.py -v
```
