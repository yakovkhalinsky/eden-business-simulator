# Eden Business Simulator

A greenfields business simulator framework that emits realistic event streams one business domain at a time. Built for external tool evaluation. Supports durable, continuous generation with SQLite/NDJSON persistence and replay.

## Quick start

Requires Python 3.12 and `uv`.

```bash
uv sync
uv run eden-business-simulator run ecommerce --duration 60 --rate 2 --seed 42
```

Pipe the NDJSON output to an evaluator:

```bash
uv run eden-business-simulator run ecommerce --duration 30 --rate 5 --seed 42 --no-realtime | ./my-evaluator
```

## Available business types

- `cafe` — hospitality events (shift open/close, staff, menu, supplier deliveries, tables, orders, KDS tickets, payments, loyalty, wastage, stock counts)
- `clinic` — outpatient healthcare events (appointments, encounters, vitals, diagnoses, procedures, labs, prescriptions, claims, payments, referrals, no-shows)
- `ecommerce` — online retail events (customers, products, carts, orders, payments, shipping, refunds, inventory)
- `electrician` — dispatched electrical trade events (tickets, assignment, dispatch, diagnosis, parts, work completion, invoices, payments, follow-ups)
- `field_service` — dispatched technician events (tickets, assignment, dispatch, diagnosis, parts, work completion, invoices, payments, follow-ups)
- `gym` — fitness studio events (memberships, check-ins, class booking/attendance, PT sessions, workouts, progress, retail, billing, churn)
- `plumber` — dispatched plumbing trade events (tickets, assignment, dispatch, diagnosis, parts, work completion, invoices, payments, follow-ups)
- `logistics` — last-mile delivery events (shipments, routes, drivers, stops, delivery attempts, POD, exceptions, feedback, returns, fuel)
- `saas` — SaaS subscription business (account signup, plans, feature usage, invoices, payments, support tickets, churn)

All monetary sale, invoice, and payment events include `tax_amount` and `tax_type` fields. The default tax is `GST` at 10%.

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

## Documentation

- [`docs/setup.md`](docs/setup.md) — installation, CLI usage, daemon/replay/status, output modes, evaluator integration, determinism, and stream ID guidance.
- [`docs/businesses/`](docs/businesses/) — one guide per supported business type with domain overview, systems, workflow, event catalog, and example payloads.
- [`docs/architecture.md`](docs/architecture.md) — event envelope, clock, runners, storage/output adapters, `BusinessSimulator` lifecycle, and framework helpers.
- [`docs/adding_a_business.md`](docs/adding_a_business.md) — runbook for adding a new simulator and updating the docs.

## Tests

```bash
uv run pytest
```
