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

## Business definitions (Phase 0, configuration-driven runtime)

Each business has a versioned JSON definition in
`src/eden_business_simulator/configs/<business_type>.json` (`schema_version: 1`),
validated by the Pydantic models in `framework/definition.py`. A definition
declares the event catalogue (base weights and guard *names*), dayparts,
domain data (menus, plans, parts, price lists), and the declared
initial-state keys, defaults and types.

Guard and emitter logic stays in code: `framework.definition.build_weighted_catalog`
resolves a definition's guard names against the guard callables a simulator
supplies, and an unknown guard name, a negative weight, a duplicate or
missing event, or an unknown daypart target is a hard validation failure.
`BusinessSimulator.configure()` loads the definition, validates
`SimulatorConfig.initial_state_overrides` against the declared keys (unknown
keys fail), and records `definition_hash` (a stable SHA-256 of the canonical
definition JSON) so a run can be tied to the exact configuration that
produced it. Tuning a business — weights, dayparts, domain data, initial
state — is therefore a config change; adding a new guard, emitter or payload
shape remains a code change.

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

### Position snapshots (Phase 1, state read API)

`position_snapshot()` is separate from `state_snapshot()`: where
`state_snapshot()` is checkpoint/resume state, `position_snapshot()` is
the read API for external consumers — where the business stands right
now (cash, stock, open work). It returns a versioned envelope
`{"schema_version": 1, "business_type": ..., "position": {...}}` with
per-business sections; runners/CLI add stream/sequence around it.

Exposure: (i) `ContinuousRunner` writes the position at every
checkpoint under the snapshot key `"position"` (the `"latest"`
snapshot/checkpoint/restore path is untouched), readable via the
`position` CLI command; (ii) opt-in in-stream events —
`SimulatorConfig.position_snapshot_every_events` (0 = off, the
default) makes both runners emit a `state_snapshot_recorded` event
whose payload is the position envelope every N business events.

### Period summaries (Phase 2, KPI events)

When a business definition declares `kpis`, both runners feed every
business event to a `PeriodTracker` (`framework/kpi.py`) and emit a
`period_summary` event at each simulated calendar-day boundary (the
summary is emitted *before* the first event of the new day, so
snapshot KPIs read the position exactly as the closed period ended),
plus a final `"partial": true` summary at run end for the last open
period if it contains at least one business event. Closed periods
carry `"partial": false`. Like snapshot events, summaries get a
normal sequence but do not count toward `max_events`.
`SimulatorConfig.period_summaries_enabled` (default true) turns this
off; simulators without KPI definitions are unaffected. Cafe's
`shift_closed` event is unchanged.

KPI definitions are config (the `kpis` list in each business's JSON
definition) over a small vocabulary: `count`, `count_where`,
`sum`, `sum_where` (stream aggregations over a payload field,
optionally filtered on one field), `ratio` (two sibling KPIs;
divide-by-zero yields 0.0), `snapshot` (a dotted field of
`position_snapshot()` at period close), and `computed` (a named
handler registered in `framework/kpi.py` — the escape hatch). The
summary payload carries `period_start`/`period_end` (exclusive),
`sequence_start`/`sequence_end` of the period's business events,
`event_counts`, the computed `kpis`, and the definition
version/hash, so an external verifier can recompute every
stream-based KPI from the raw events in that sequence range.

### Action channel (Phase 3, cafe pilot)

Simulators accept management actions through `apply_action(request,
clock)` on the base class. The action *contract* is config: each
business definition declares an `actions` list (`action_type`, the
`effect` handler name, `effect_event_type`, `requires_approval`, and
a parameter schema with types, required flags, `min`/`max` limits and
`enum`s). The shared pipeline rejects, in order: unknown actions,
duplicate `action_id`s (idempotency — a second application returns
`already_applied` with no effect), actions flagged
`requires_approval` whose request carries no `approved_by` stamp
(Eden records approval, it does not grant it), and invalid parameters
(reasons name the offending parameter). Only then does it dispatch to
the simulator's `_apply_<action_type>` handler, which must validate
before mutating and consume no RNG on a rejection path — so a
rejected action leaves state and the deterministic stream untouched,
and only a successful effect records the `action_id`.

Runners apply actions *between ticks* (at a defined sequence point):
both runners accept scripted `actions=[(sequence, ActionRequest)]`
for deterministic runs, and `ContinuousRunner` also exposes a
thread-safe `submit_action()` queue drained in FIFO order before the
next business event. For each action the runner emits the domain
effect event(s) first, then an audit event — `action_applied`
(naming the effect event types) or `action_rejected` (with the
reason; duplicates are audited here too, with status
`already_applied`). Action events get normal sequences and are
persisted/output like any event, but do not count toward
`max_events`. Effect events whose type is a catalogued business
event type are observed by the KPI tracker (an action's supplier
delivery really happened); audit events and new action-only effect
types (e.g. cafe's `menu_price_adjusted`) are not. Effect event
types need not appear in `available_event_types()`, which lists only
catalogue-emitted types.

Phase 3 ships the cafe pilot: `place_supplier_order`,
`adjust_price` (approval-gated), `clock_staff_in`, `clock_staff_out`.

Phase 4a adds the gym and clinic catalogues. Gym: `freeze_membership`
and `unfreeze_membership` (action-only effect `membership_unfrozen`),
`schedule_class`, `cancel_class` (action-only effect
`class_session_cancelled`; cancelled classes are excluded from
`class_booked` selection), `send_retention_outreach`, and
`adjust_plan_price` (approval-gated; reprices the plan and every
existing membership on that tier, action-only effect
`plan_price_adjusted`). Clinic: `schedule_appointment`,
`cancel_appointment`, `reschedule_appointment` (all reusing the
catalogued appointment event types), `add_provider_shift`
(action-only effect `provider_shift_added` — a roster-availability
record only; the clinic simulation has no shift state that gates
events), and `chase_claim` (action-only effect `claim_chased`,
tracked as `chase_count`/`last_chased_at` on the claim).

Phase 4b adds the trades catalogue, shared by `field_service`,
`electrician` and `plumber`: one handler implementation on
`FieldServiceSimulator`, inherited unchanged by the other two, with
the same six actions declared in all three definitions —
`assign_technician`, `dispatch_technician`, `order_parts`,
`approve_estimate`, `chase_invoice` (action-only effect
`invoice_chased`, tracked as `chase_count`/`last_chased_at` on the
invoice) and `schedule_follow_up`. The businesses differ only by
their configured categories and parts catalogues, and the handlers
honour that: `order_parts` validates the part against the acting
business's own catalogue, so a part valid for one trade is rejected
as unknown by another. None of the trades actions requires approval
— assigning, dispatching, ordering parts, approving an estimate,
chasing an invoice and scheduling a follow-up are routine
operations.

Phase 4c completes the catalogues — all nine businesses now declare
actions. Ecommerce: `adjust_price` (approval-gated; action-only
effect `product_price_adjusted`), `restock_product` (reusing the
catalogued `inventory_adjusted`), `issue_refund` (approval-gated,
reusing `refund_issued`; only paid or shipped orders are
refundable), and `expedite_order` (action-only effect
`order_expedited`; paid orders only). SaaS: `chase_invoice`
(action-only effect `invoice_chased`, tracked as
`chase_count`/`last_chased_at` on the invoice), `change_plan_price`
(approval-gated; reprices the plan and every existing subscription
on it, so position MRR moves — action-only effect
`plan_price_changed`), `freeze_account` / `unfreeze_account`
(action-only effects `account_frozen` / `account_unfrozen`; a frozen
subscription drops out of the active count and MRR until unfrozen),
`offer_discount` (approval-gated; recorded on the subscription, or
on the simulator's `discounts` list for accounts without one —
action-only effect `discount_offered`), and `resolve_ticket`
(reusing the catalogued `ticket_resolved`). Logistics:
`assign_driver` (reassigns a planned route to another driver,
reusing the catalogued `driver_assigned`), `replan_route`
(action-only effect `route_replanned`; reverses the route's stop
order and renumbers the `sequence` on the stop records),
`expedite_shipment` (action-only effect `shipment_expedited`;
refused once a shipment is delivered), and `initiate_return`
(reusing the catalogued `return_initiated`; delivered shipments
only).

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
