# Scenario packs (Phase 6)

Scenario packs are time-boxed disruptions an operator can schedule
into a run: a supplier failure, a sick day, a demand surge. They exist
so an agent managing a business has something to *respond* to, not
just a steady-state stream to watch.

A pack is pure configuration. Packs are declared in
[`src/eden_business_simulator/configs/scenarios.json`](../src/eden_business_simulator/configs/scenarios.json)
and validated by `ScenarioDef` in `framework/scenario.py`. A pack is a
name, a description, a default duration, and a list of effects — each
effect multiplies the catalogue selection weight of one event type
while the pack is active:

```json
{"event_type": "churned", "multiplier": 4.0}
```

A multiplier of `0` suppresses the event type entirely. A business
whose catalogue lacks a targeted type is simply unaffected, so one
pack can span several businesses. Overlapping packs that target the
same type stack multiplicatively.

## The v1 packs

| Pack | Default duration | Effects (event type × multiplier) |
| --- | --- | --- |
| `supplier_failure` | 2880 min (2 days) | cafe `supplier_delivery_received` ×0.1, cafe `stock_count_recorded` ×3 |
| `staff_sick_day` | 480 min (8 hours) | cafe `staff_clocked_in` ×0.25 |
| `demand_spike` | 1440 min (1 day) | cafe `order_taken` ×2.5, ecommerce `order_placed` ×2.5, gym `check_in_recorded` ×2.5, gym `membership_enrolled` ×2.5, logistics `shipment_created` ×2.5 |
| `churn_wave` | 2880 min (2 days) | saas `churned` ×4, gym `membership_cancelled` ×4 |
| `payment_failure_wave` | 1440 min (1 day) | saas `payment_failed` ×4, gym `payment_failed` ×4 |

Notes on target choices — multipliers can only key on event *types*,
never on payload outcomes:

- The saas churn event type is `churned` (there is no
  `subscription_cancelled` type).
- Ecommerce payment failures are a payload outcome of
  `payment_processed`, and clinic claim denials are a payload status
  of `claim_adjudicated`, so `payment_failure_wave` cannot target
  them; it covers the two businesses with a real `payment_failed`
  event type (saas, gym).
- No catalogue has a dedicated staff-absence event type, so
  `staff_sick_day` manifests as far fewer cafe clock-ins.
- The brief's `stock_low` signal does not exist as an event type; the
  closest real type is the cafe's `stock_count_recorded`, which
  `supplier_failure` boosts as shortages bite.

## Scheduling

Scenarios are scheduled, not actions — they are part of a run's
configuration (`SimulatorConfig.scenario_schedule`), never submitted
through the Phase 3 action channel or the Phase 5 command table. Each
schedule entry is:

```json
{"name": "churn_wave", "start_offset_minutes": 0, "duration_minutes": 120}
```

- `name` — a pack from `configs/scenarios.json`. An unknown name is a
  configuration error raised before any event is generated (the
  config validator and the engine both fail fast).
- `start_offset_minutes` — simulated minutes after run start
  (default `0`: active from the first event).
- `duration_minutes` — optional override of the pack's default
  duration.

From the CLI, both the batch and daemon commands accept repeatable
`--scenario` flags (starting at run start) plus a shared
`--scenario-duration` override:

```bash
uv run eden-business-simulator run cafe \
  --scenario demand_spike --scenario supplier_failure \
  --scenario-duration 720 --no-realtime

uv run eden-business-simulator daemon saas \
  --scenario churn_wave --storage sqlite --storage-uri saas.db
```

## Markers

When the simulated clock crosses a scheduled start, the runner emits
a `scenario_started` event:

```json
{
  "scenario": "churn_wave",
  "description": "Customers leave in a wave: ...",
  "started_at": "2026-01-01T08:00:00+00:00",
  "ends_at": "2026-01-03T08:00:00+00:00",
  "duration_minutes": 2880,
  "effects": [{"event_type": "churned", "multiplier": 4.0}]
}
```

At expiry it emits `scenario_ended` with `scenario`, `ended_at`, and
`duration_minutes`. Markers are framework events in the
`period_summary` family: in the continuous runner they consume a
sequence but never a `max_events` slot, and they are never observed
by the KPI tracker — period summaries reconcile from the raw business
events exactly as they do without scenarios.

## Determinism

- With no scenario active, selection is byte-identical to an
  unscheduled run: the multiplier is exactly `1.0`, no weights change,
  and no extra RNG draws are made. The golden-stream suite is the
  standing proof.
- Per D10, each activation derives a separate scenario RNG stream
  seeded from `(run seed, scenario name)`. Scenario-internal
  randomness therefore can never perturb the business stream. The v1
  packs are pure multipliers and draw nothing from it; the stream
  exists so future packs have somewhere deterministic to draw from.

## Honest scope

- **V1 packs are weight multipliers only.** They change how *often*
  event types are selected; they do not mutate business state. A
  supplier going permanently bankrupt, a staff member who stays home
  for a week of shifts, or a scenario triggered mid-run by an
  operator action are future work.
- **Setup-queue caveat (cafe).** The cafe emits its opening
  `shift_opened`, staff clock-ins, menu items, and one
  `supplier_delivery_received` per ingredient through a deterministic
  setup queue that bypasses weighted selection. Multipliers on those
  types therefore govern only catalogue-chosen occurrences — for the
  cafe's opening setup they do not bite. `supplier_failure`'s
  observable cafe effect in v1 is the stock-counting surge (and any
  catalogue-chosen deliveries in businesses that select them);
  `staff_sick_day`'s multiplier is fully wired through selection, as
  the eligible-weight tests show, but a standard cafe shift still
  clocks its opening crew in. Modelling absence inside the setup
  queue belongs with the state-mutating scenarios above.
- A daemon resumed from a checkpoint restarts its scenario schedule
  from the resume point (offsets are relative to run start); markers
  for already-started packs are not replayed.
