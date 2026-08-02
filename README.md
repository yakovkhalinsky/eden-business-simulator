# Eden Business Simulator

A greenfields business simulator framework that emits realistic event streams one business domain at a time. Built for external tool evaluation.

## Quick start

Requires Python 3.12 and `uv`.

```bash
uv sync
uv run eden-business-simulator run ecommerce --duration 60 --rate 2 --seed 42

# Run the new cafe simulator
uv run eden-business-simulator run cafe --duration 300 --rate 2 --seed 42 --no-realtime
```

Pipe the NDJSON output to an evaluator:

```bash
uv run eden-business-simulator run ecommerce --duration 30 --rate 5 | ./my-evaluator
uv run eden-business-simulator run cafe --duration 120 --rate 2 --no-realtime | ./my-evaluator
```

## Available business types

- `cafe` — hospitality events (shift open/close, staff, menu, supplier deliveries, tables, orders, KDS tickets, payments, loyalty, wastage, stock counts)
- `ecommerce` — online retail events (customers, products, carts, orders, payments, shipping, refunds, inventory)
- `saas` — minimal SaaS stub (account signup and feature usage)

## Output modes

- `ndjson` (default) — one JSON event envelope per line to stdout
- `http` — POST each event to a webhook URL (`--output http --webhook-url URL`)

## Project layout

See `PLAN.md` and `docs/adding_a_business.md`.

## Tests

```bash
uv run pytest
```
