# Architecture

This document describes the runtime architecture of the Eden Business Simulator: how configuration becomes an event stream, how events are wrapped and persisted, and how the reusable framework helpers fit together.

## High-level data flow

```text
CLI command
   |
   v
SimulatorConfig
   |
   v
load_simulator(business_type)  -->  BusinessSimulator subclass
   |
   v
Runner (batch)  or  ContinuousRunner (persistent)
   |
   +--> OutputAdapter (ndjson, http, none)
   +--> StorageAdapter (sqlite, ndjson, memory)
```

The CLI parses arguments into a `SimulatorConfig`, loads the registered simulator for the requested `business_type`, seeds it, and then drives it through a runner. Batch runs use `Runner`; long-running or resumable runs use `ContinuousRunner`.

## `EventEnvelope` and version contract

Every emitted event is wrapped in a Pydantic `EventEnvelope`:

```json
{
  "event_id": "28e42171-5979-4357-95ce-6aadedd1efe9",
  "timestamp": "2026-08-02T13:24:28.739745Z",
  "business_type": "cafe",
  "event_type": "shift_closed",
  "payload": { ... },
  "version": "1.0"
}
```

When the event is persisted as part of a stream, two optional fields are added:

- `stream_id` — the logical stream identifier.
- `sequence` — the monotonic offset within that stream (`0`, `1`, `2`, ...).

`to_json_line()` serializes the envelope as JSON while omitting `null` fields, which is why batch-run envelopes do not include `stream_id` or `sequence`.

The `version` field is currently always `"1.0"`. Consumers can use it to detect future envelope changes.

## `Clock`: simulated time vs wall-clock time

`Clock` tracks simulated time:

- `start_time` — the simulated starting moment (defaults to `datetime.now(timezone.utc)`).
- `now` — the current simulated moment.
- `tick_seconds` — how much simulated time advances per event.

The runner sets `tick_seconds = 1.0 / events_per_second`. Wall-clock pacing is performed separately by the runner, so tests can run in zero real time when `realtime=False`.

All event `timestamp` values come from `clock.now`, not from wall-clock time, unless the simulator specifically includes wall-clock metadata in its payload.

## `Runner` and `ContinuousRunner`

### `Runner`

Used by the `run` CLI command. It:

1. Creates a `Clock` with the requested rate.
2. Calls `simulator.next_event(clock)` in a loop.
3. Wraps each result in an `EventEnvelope`.
4. Writes the envelope through the selected `OutputAdapter`.
5. Stops when `duration_seconds` or `max_events` is reached.

`Runner` is stateless across invocations; a new run always starts from sequence 0.

### `ContinuousRunner`

Used by the `daemon` CLI command. It:

1. Installs SIGTERM/SIGINT handlers for clean shutdown.
2. Loads the latest checkpoint and snapshot for the stream.
3. Calls `simulator.restore(snapshot_data)` if the simulator implements the optional hook.
4. Persists every event through a `StorageAdapter` with monotonic `sequence`.
5. Optionally re-emits the event through an `OutputAdapter`.
6. Writes checkpoints periodically and on shutdown.
7. Stops on signal, `max_events`, or `duration_seconds`.

Resume is deterministic because the stream resumes from the original seed and the last checkpoint sequence. Stateful simulators (for example `gym`) override `restore()` to reload counters or other mutable state.

## Storage adapters

All storage adapters implement the abstract `StorageAdapter` contract in `src/eden_business_simulator/storage/base.py`.

| Adapter | Persistence | Snapshots/checkpoints | Best for |
|---------|-------------|----------------------|----------|
| `SqliteStorageAdapter` | SQLite database, WAL mode, `event_log` table | `snapshots` and `checkpoints` tables | Default durable backend. |
| `NdjsonStorageAdapter` | Append-only `.jsonl` file | Companion `.meta.json` file | Human-readable logs, easy to tail. |
| `MemoryStorageAdapter` | In-memory list | In-memory dict | Unit tests and ephemeral streams. |

Important distinctions:

- `offset` is the storage-level position (row ID in SQLite, byte offset in NDJSON).
- `sequence` is the logical event number within a stream, assigned by the adapter.
- `stream_id` isolates different event streams in the same storage file.

New backends can be added by subclassing `StorageAdapter` and registering them in `src/eden_business_simulator/storage/__init__.py`.

## Output adapters

Output adapters are lightweight sinks for batch-mode emission:

- `NDJsonOutputAdapter` — writes `EventEnvelope.to_json_line()` plus a newline to a text stream (stdout by default).
- `HttpOutputAdapter` — POSTs each envelope as JSON to a webhook URL using `httpx`.
- `none` — no output adapter; used with `daemon` when only storage is needed.

HTTP output posts one event per request. For very high throughput, a batched adapter is a future extension point.

## `BusinessSimulator` base class and lifecycle

All simulators inherit from `BusinessSimulator` in `src/eden_business_simulator/businesses/base.py`.

```python
class BusinessSimulator(ABC):
    business_type: str = "abstract"

    def configure(self, config: SimulatorConfig) -> None: ...
    def initialize(self, seed: int) -> None: ...
    def available_event_types(self) -> list[str]: ...
    def next_event(self, clock: Clock) -> dict[str, Any]: ...
    def state_snapshot(self) -> dict[str, Any]: ...
    def restore(self, snapshot: dict[str, Any]) -> None: ...  # optional
```

Lifecycle:

1. `configure(config)` — receives the `SimulatorConfig` before the run starts.
2. `initialize(seed)` — seeds `random.Random` and `faker.Faker`, builds catalogs, creates initial entities.
3. `available_event_types()` — returns the full catalog of event types the simulator may emit.
4. `next_event(clock)` — returns `{"event_type": ..., "payload": ...}` for the current simulated moment.
5. `state_snapshot()` — returns a serializable snapshot of internal state for checkpoints.
6. `restore(snapshot)` — optional hook to resume from a snapshot during daemon startup.

Simulators must never rely on global random state. They should create local `random.Random` and `faker.Faker` instances seeded from `initialize(seed)`.

## Framework helpers

The `eden_business_simulator.framework` package provides reusable components for common simulation patterns.

### `ids.IdGenerator`

Produces deterministic, human-readable IDs such as `ord_0001`. Counters advance per prefix, so as long as the simulator performs the same operations for the same seed, ID sequences match.

### `actors.ActorPool`, `StaffRoster`, `MenuCatalog`

- `ActorPool` — generic deterministic pool of named entities (customers, members, patients, etc.).
- `StaffRoster` — staff with roles and clock-in state.
- `MenuCatalog` — menu items with recipes/prices.

### `inventory.RecipeBook`, `InventoryLedger`

- `RecipeBook` — maps items to their ingredient bills of materials.
- `InventoryLedger` — tracks stock, receives deliveries, auto-deducts ingredients when items are prepared, records wastage, and records stock counts.

### `scheduler.DaypartScheduler`

Varies event weights by simulated clock hour. Each `Daypart` defines a time range and per-event weight modifiers. Simulators combine the scheduler with `WeightedEventCatalog` so that, for example, cafe orders spike during breakfast and lunch rushes.

### `state_machine.TransitionModel`

Tracks entity lifecycle states and allowed transitions. Used by the cafe simulator to move orders and kitchen tickets through statuses such as `new` -> `fired` -> `ready` -> `paid`.

### `catalog.WeightedEventCatalog`

Declarative event selector:

- Register event types with a `base_weight`.
- Add `guard` functions so an event is only eligible when simulator state permits it.
- Add `time_modifier` functions or pass a `DaypartScheduler` to vary weights by hour.
- `choose(hour, context)` returns a weighted-random eligible event type.

## Determinism guarantees

A run is deterministic when:

- The same `seed` is supplied.
- The same `business_type` and `SimulatorConfig` are used.
- The run is non-realtime or is replayed from storage (replay reproduces stored timestamps).

The simulator initializes its local RNG and Faker from the seed, and framework helpers reuse those instances. Storage adapters assign monotonic sequences, and `ContinuousRunner` resumes from the same seed plus the checkpointed sequence.

## Further reading

- Per-business event catalogs: [`docs/businesses/`](businesses/)
- Setup and CLI examples: [`docs/setup.md`](setup.md)
- How to add a simulator: [`docs/adding_a_business.md`](adding_a_business.md)
