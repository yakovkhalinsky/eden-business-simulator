# Documentation plan

Goal: add comprehensive documentation covering each supported business type in the Eden Business Simulator, including the systems, workflows, and realistic events/data generated. Also improve the how-to guide for setting up and adding new business simulators.

## Proposed documentation tree

- `README.md` — concise quick-start plus a "Documentation" index.
- `docs/setup.md` — installation, basic `run` usage, daemon mode with storage configuration (sqlite, ndjson), replay/status/checkpoint commands, output modes, evaluator integration patterns, determinism/seed guidance, and common `--stream-id` guidance.
- `docs/architecture.md` — `EventEnvelope` contract, `Clock`, `Runner` vs `ContinuousRunner`, `BusinessSimulator` lifecycle, output adapters, storage adapters, and framework helpers.
- `docs/adding_a_business.md` — contributor runbook cross-referencing user docs and architecture, covering lifecycle and the optional `restore(snapshot)` hook, with a minimal helper-based example.
- `docs/businesses/ecommerce.md`
- `docs/businesses/saas.md`
- `docs/businesses/cafe.md`
- `docs/businesses/gym.md`
- `docs/businesses/logistics.md`
- `docs/businesses/field_service.md`
- `docs/businesses/clinic.md`

## Source references used

All event types and example payload structures were derived from the current source code in `src/eden_business_simulator/businesses/` and the framework/storage/runner modules. Known quirks documented honestly include:

- `SaaSSimulator` is a minimal stub; only `account_signed_up` has a dedicated payload.
- `field_service` `payment_received.invoice_id` and `clinic` `lab_result_received.lab_order_id` are freshly generated, not strict references to prior events.
- `cafe` `shift_closed` and `logistics` `fuel_stop_logged` are low-probability, end-of-phase events.

## Verification criteria

1. All files in the documentation tree exist.
2. `README.md` links to `docs/setup.md`, `docs/businesses/`, `docs/architecture.md`, and `docs/adding_a_business.md`.
3. Every event type returned by each simulator's `available_event_types()` appears in its per-business doc.
4. Example payloads match the current code (captured from actual simulator runs where possible).
5. `uv run pytest` passes.

## Implementation notes

- A lightweight test (`tests/test_documentation.py`) checks file existence, README links, and event-type coverage.
- The existing `tests/test_gym.py` was adjusted from 80 to 120 iterations so that the low-probability `membership_cancelled` event reliably appears for the fixed test seed.
