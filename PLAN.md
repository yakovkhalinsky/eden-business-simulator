# Eden Business Simulator — Implementation Plan

Goal: `d044fee0-5512-45c1-8921-9d174c4cc3f9`

Create a greenfields business simulator framework that can generate realistic actions and data based on different business types. The simulators will be used as evaluation targets to test external tools against simulated "real life" businesses. Only one simulator needs to run at a time.

## Technology stack

- Python 3.12
- `uv` for project/dependency/lockfile management
- Pydantic v2 for config and event schemas
- Typer for CLI
- Faker / Mimesis for realistic entity data
- structlog for structured logs (kept separate from event stream)
- httpx for optional HTTP output
- pytest for tests

## Repository layout

```
/home/yakov/git/eden-business-simulator/
  PLAN.md
  README.md
  pyproject.toml
  uv.lock
  src/eden_business_simulator/
    __init__.py
    cli.py                 # Typer entry point
    config.py              # Pydantic SimulatorConfig
    models.py              # EventEnvelope, Actor, Clock
    runner.py              # Time/rate control and dispatch loop
    output/
      __init__.py
      base.py              # OutputAdapter protocol
      ndjson.py            # stdout NDJSON adapter
      http.py              # POST adapter to evaluator webhook
    businesses/
      __init__.py          # registry and load_simulator()
      base.py              # BusinessSimulator abstract class
      ecommerce.py         # E-commerce retail simulator
      saas.py              # SaaS subscription simulator
  tests/
    test_ecommerce.py
    test_saas.py
    test_runner.py
  docs/
    adding_a_business.md
```

## Module boundaries

- `models`: canonical event envelope and shared primitives.
- `businesses.base`: abstract contract (`configure`, `initialize`, `available_event_types`, `next_event`, `state_snapshot`).
- `businesses.{ecommerce,saas}`: concrete domain logic; own actor sets and event probability tables.
- `runner`: loads one simulator by name, advances simulated/real clock, emits events through output adapters.
- `output`: pluggable stdout/HTTP adapters; evaluator consumes the stream.
- `cli`: wires config → runner → output.

## Simulator interface/contract

Configuration: a Pydantic `SimulatorConfig` accepted via CLI args, env vars, or YAML/JSON file.
Key fields: `business_type`, `duration_seconds` OR `max_events`, `events_per_second`, `seed`, `output_mode`, `webhook_url`, `initial_state_overrides`.

Lifecycle:

```python
sim = load_simulator(config.business_type)
sim.configure(config)
sim.initialize(seed)  # sets initial deterministic state
while runner.should_continue():
    event = sim.next_event(clock)
    adapter.write(EventEnvelope.from_event(event))
```

Emission format: one canonical `EventEnvelope` per line (NDJSON):

```json
{"event_id":"uuid","timestamp":"2026-08-02T12:00:00Z","business_type":"ecommerce","event_type":"order_placed","payload":{...}}
```

Default output: stdout NDJSON so an evaluator can simply pipe stdin.
Optional output: HTTP POST to evaluator webhook (`--output http --webhook URL`).

## Business domain candidates

### 1. E-commerce retail

Events: `customer_created`, `product_viewed`, `cart_updated`, `order_placed`, `payment_processed`, `order_shipped`, `inventory_adjusted`, `refund_issued`.

Sample payloads:

- `customer_created`: `{customer_id, name, email, registered_at}`
- `order_placed`: `{order_id, customer_id, items: [{product_id, sku, qty, unit_price}], placed_at, total}`
- `payment_processed`: `{order_id, payment_id, amount, currency, status, processed_at}`
- `inventory_adjusted`: `{product_id, sku, delta, reason, adjusted_at}`

### 2. SaaS subscription

Events: `account_signed_up`, `plan_subscribed`, `feature_used`, `invoice_generated`, `payment_succeeded`, `payment_failed`, `support_ticket_opened`, `ticket_resolved`, `churned`.

Sample payloads:

- `account_signed_up`: `{account_id, email, signup_at, attribution}`
- `plan_subscribed`: `{account_id, subscription_id, plan_id, mrr, started_at}`
- `feature_used`: `{account_id, event_id, feature_key, quantity, recorded_at}`
- `invoice_generated`: `{account_id, invoice_id, amount, due_date, line_items}`

## Running one business type at a time

The CLI accepts exactly one positional `business_type` argument. A registry maps names to classes under `businesses/`; `load_simulator` returns the chosen class instance. The runner owns a single simulator instance; there is no multi-process or multi-thread simulator concurrency. Adding a new domain means adding a module that subclasses `BusinessSimulator` and registering its name.

## Risks and open questions

- **Bidirectional control**: Does the external evaluator need to send commands back into the simulator (e.g., approve refunds, change plan)? If yes, add a stdin command protocol or small HTTP control API. Needs requester input.
- **State inspection**: Should the simulator expose queryable current state for the evaluator, or only the event stream? Recommend state snapshots on request or periodic checkpoint file. Needs confirmation.
- **Packaging**: Is a Docker image required immediately, or is a pip/uv package sufficient? Recommend both.
- **Determinism vs replay**: Use a single seed for reproducible runs, but time-based jitter may require a simulated clock. Decide whether timestamps should be wall-clock or simulated.
- **Domain priority**: E-commerce is proposed as the first domain because it has a broad entity/event surface.

## Builder deviations and notes

- `EventEnvelope` includes a `version` field (default `"1.0"`) for stream compatibility without breaking the core shape.
- The CLI adds `--realtime/--no-realtime` so tests and fast pipelines can disable wall-clock pacing.
- The HTTP output adapter currently POSTs each event individually; batching can be added later if throughput becomes a constraint.
- The `saas` domain is implemented as a minimal stub in this first pass; it emits `account_signed_up` and `feature_used` events and will be expanded in a follow-up.
- Payload timestamps are generated from the simulated `Clock` and serialized as ISO-8601 strings; envelope timestamps are handled by Pydantic and rendered with `Z` UTC notation.
