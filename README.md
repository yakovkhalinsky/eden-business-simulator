# Eden Business Simulator

A greenfields business simulator framework that emits realistic event streams one business domain at a time. Built for external tool evaluation. Now supports durable, continuous generation with SQLite/NDJSON persistence and replay.

## Quick start

Requires Python 3.12 and `uv`.

```bash
uv sync
uv run eden-business-simulator run ecommerce --duration 60 --rate 2 --seed 42

# Run the cafe simulator
uv run eden-business-simulator run cafe --duration 300 --rate 2 --seed 42 --no-realtime

# Run the gym simulator
uv run eden-business-simulator run gym --duration 300 --rate 2 --seed 42 --no-realtime

# Run the logistics simulator
uv run eden-business-simulator run logistics --duration 300 --rate 2 --seed 42 --no-realtime
```

Pipe the NDJSON output to an evaluator:

```bash
uv run eden-business-simulator run ecommerce --duration 30 --rate 5 | ./my-evaluator
uv run eden-business-simulator run gym --duration 120 --rate 2 --no-realtime | ./my-evaluator
```

## Available business types

- `cafe` — hospitality events (shift open/close, staff, menu, supplier deliveries, tables, orders, KDS tickets, payments, loyalty, wastage, stock counts)
- `ecommerce` — online retail events (customers, products, carts, orders, payments, shipping, refunds, inventory)
- `gym` — fitness studio events (memberships, check-ins, class booking/attendance, PT sessions, workouts, progress, retail, billing, churn)
- `logistics` — last-mile delivery events (shipments, routes, drivers, stops, delivery attempts, POD, exceptions, feedback, returns, fuel)
- `saas` — minimal SaaS stub (account signup and feature usage)

## Output modes

- `ndjson` (default) — one JSON event envelope per line to stdout
- `http` — POST each event to a webhook URL (`--output http --webhook-url URL`)
- `none` — suppress stdout output (useful with `daemon` when only persistence is needed)

## Continuous daemon mode and replay

Generate events continuously and persist them to SQLite (default) or NDJSON:

```bash
# Run until SIGTERM/SIGINT, persisting every event
uv run eden-business-simulator daemon gym \
  --stream-id gym_demo_seed42 \
  --rate 2 \
  --storage sqlite \
  --storage-uri gym_demo.db

# Replay the stored stream at original speed
uv run eden-business-simulator replay gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db --speed 1.0

# Fast-forward replay
uv run eden-business-simulator replay gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db --speed 10.0

# List persisted streams
uv run eden-business-simulator status --storage sqlite --storage-uri gym_demo.db

# Force a checkpoint
uv run eden-business-simulator checkpoint gym gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db
```

The daemon writes periodic checkpoints and a final checkpoint on shutdown so it can resume from the last sequence without duplicating events.

## Project layout

See `docs/realtime_and_research_plan.md` and `docs/adding_a_business.md`.

## Tests

```bash
uv run pytest
```
