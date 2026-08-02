# Eden Business Simulator

A greenfields business simulator framework that emits realistic event streams one business domain at a time. Built for external tool evaluation.

## Quick start

Requires Python 3.12 and `uv`.

```bash
uv sync
uv run eden-business-simulator run ecommerce --duration 60 --rate 2 --seed 42
```

Pipe the NDJSON output to an evaluator:

```bash
uv run eden-business-simulator run ecommerce --duration 30 --rate 5 | ./my-evaluator
```

## Available business types

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
