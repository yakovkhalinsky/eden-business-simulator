# Setup and runbook

This guide covers installing the Eden business simulator, generating event streams, and integrating the output into evaluator workflows.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/) package manager

## Install dependencies

From the project root:

```bash
uv sync
```

This creates a virtual environment and installs all locked dependencies.

## Discover supported business types

```bash
uv run eden-business-simulator list-types
```

The current release registers: `cafe`, `clinic`, `ecommerce`, `field_service`, `gym`, `logistics`, `saas`.

## Batch generation with `run`

The `run` command emits a bounded stream of events as fast as possible or paced to real time.

```bash
uv run eden-business-simulator run ecommerce --duration 60 --rate 2 --seed 42 --no-realtime
```

Useful flags:

| Flag | Description |
|------|-------------|
| `--duration`, `-d` | Simulated seconds to run (default `60`). |
| `--rate`, `-r` | Events per simulated second (default `2`). |
| `--max-events`, `-n` | Hard cap on the number of emitted events. |
| `--seed`, `-s` | Random seed; the same seed produces the same event sequence when combined with `--no-realtime`. |
| `--realtime/--no-realtime` | Pace against wall-clock time or emit as fast as possible. |
| `--output`, `-o` | `ndjson` (default), `http`, `http-batch`, or `none`. |
| `--webhook-url`, `-w` | Webhook URL when `--output http` or `http-batch`. |
| `--batch-size` | Events per POST for `http-batch` (default `50`). |
| `--flush-interval` | Seconds before a partial `http-batch` buffer is flushed (default `5`). |
| `--max-retries` | Total delivery attempts for HTTP outputs (default `3`). |
| `--dead-letter-path` | File undeliverable HTTP events are appended to (default `eden-dead-letter.ndjson`). |

The default `ndjson` output writes one JSON `EventEnvelope` per line to stdout, so it is easy to pipe into another process.

```bash
uv run eden-business-simulator run gym --duration 120 --rate 2 --no-realtime | ./my-evaluator
```

### Output modes

- `ndjson` — one JSON event envelope per line to stdout.
- `http` — POST each event envelope as JSON to `--webhook-url`.
- `http-batch` — buffer envelopes and POST them as JSON arrays to `--webhook-url`. A batch is sent when it reaches `--batch-size` events, when `--flush-interval` seconds have elapsed since the first buffered event (checked on each write; there are no background threads), or at run end, when the partial batch is flushed. Event order is preserved within and across batches.
- `none` — suppress stdout output. This is mainly useful with `daemon` when only persistence matters.

### HTTP delivery hardening

Both HTTP outputs share one delivery policy:

- **Auth** — when the `EDEN_WEBHOOK_TOKEN` environment variable is set, every request carries `Authorization: Bearer <token>`. The token is read from the environment only — never from CLI flags or config files — and is never logged. Unset, no header is sent.
- **Retry** — network errors and HTTP 5xx are retried up to `--max-retries` total attempts with exponential backoff (0.5s, doubling). HTTP 4xx fails immediately: the consumer has rejected the payload, and retrying cannot help.
- **Dead-letter** — when delivery still fails, the events are appended to the dead-letter file (default `eden-dead-letter.ndjson`, override with `--dead-letter-path`) and the run *continues*: a dead consumer never crashes or hangs a run. Each dead-lettered event is one NDJSON line — the event envelope plus a `_dead_letter` object:

```json
{"event_id": "evt-1", "event_type": "order_placed", "...": "...", "_dead_letter": {"failed_at": "2026-08-02T12:00:00+00:00", "attempts": 3, "last_status": 500, "url": "http://localhost:8080/events"}}
```

(`last_status` is replaced by `error` for network failures.)

Delivery semantics, precisely: each event is either delivered (a 2xx response) or captured in the dead-letter file. Dead-lettered events are *not* redelivered automatically, and a retried batch is only re-sent when the previous attempt provably failed before a 2xx — but a consumer that also reads storage should still dedupe by `event_id` / sequence, because a connection lost after the consumer processed a batch can make that batch arrive twice (once live, once via replay).

## Continuous daemon mode

`daemon` runs indefinitely (or until `--max-events` / `--duration` / SIGTERM / SIGINT) and persists every event to a storage backend. It also writes periodic checkpoints so the same stream can be resumed or replayed.

```bash
uv run eden-business-simulator daemon gym \
  --stream-id gym_demo_seed42 \
  --rate 2 \
  --storage sqlite \
  --storage-uri gym_demo.db
```

Key flags:

| Flag | Description |
|------|-------------|
| `--storage` | `sqlite` (default), `ndjson`, or `memory`. |
| `--storage-uri` | Path or URI for the storage backend. |
| `--stream-id` | Stable identifier for the event stream. Required for replay and status. |
| `--checkpoint-interval` | Wall-clock seconds between forced checkpoints (default `30`). |
| `--checkpoint-events` | Events between forced checkpoints (default `100`). |
| `--duration`, `-d` | Stop after this many simulated seconds (`0` = unlimited). |
| `--max-events` | Stop after this many events. |

### Storage backends

| Backend | URI example | Best for |
|---------|-------------|----------|
| `sqlite` | `gym_demo.db` | Default durable local store; supports WAL mode, replay, and status. |
| `ndjson` | `gym_demo.jsonl` | Human-readable append-only logs with a companion `.meta.json` file. |
| `memory` | `memory://` | Volatile; used mainly for tests. |

### Important: use `--stream-id` for replayable runs

If `--stream-id` is omitted, the daemon generates an identifier that includes the current UTC timestamp, for example `gym_seed42_20260802131900`. Replay and `status` need that exact value, so always pass an explicit `--stream-id` when you intend to replay or inspect a stream later.

### Operating a live daemon (Phase 5)

A running daemon also accepts management commands: an external
operator writes a command into the run's storage with the `command`
verb, the daemon polls the command table between ticks, applies or
rejects it through the normal action pipeline, and records the
outcome on the command record; `commands` lists the records. This is
the Beth/Aleph integration point — see
[docs/daemon.md](daemon.md) for the full contract (record shape,
status lifecycle, the D9 approval rule, and restart idempotency).

### Scheduling scenario packs (Phase 6)

Runs can be disrupted on a schedule with scenario packs — config-only
weight multipliers (a supplier failure, a demand spike, a churn wave)
that activate at a simulated offset and expire after a duration,
bracketed by `scenario_started`/`scenario_ended` markers:

```bash
uv run eden-business-simulator run cafe \
  --scenario demand_spike --scenario-duration 720 --no-realtime
uv run eden-business-simulator daemon saas \
  --scenario churn_wave --storage sqlite --storage-uri saas.db
```

See [docs/scenarios.md](scenarios.md) for the pack catalogue,
scheduling model, and determinism rules.

## Read a stream's current position

The daemon stores a position snapshot (cash, stock, open work — the
Phase 1 state read API) at every checkpoint. Read it back as JSON:

```bash
uv run eden-business-simulator position \
  --storage sqlite --storage-uri gym_demo.db \
  --stream-id gym_demo_seed42
```

It errors clearly if no position has been stored for that stream yet.
To also embed position snapshots in the stream itself, start the
daemon with `--position-snapshot-every-events N` (default 0 = off):
a `state_snapshot_recorded` event is then emitted every N events.

## Verify a stored run (Phase 8)

The `verify` verb independently recomputes a stored run from its raw
events — it never trusts the run's own stored aggregates:

```bash
uv run eden-business-simulator verify \
  --stream-id gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db
```

Options:

- `--stream-id` (required) — the stream to verify.
- `--storage` — `sqlite` (default) or `ndjson`. Memory runs are
  per-process and cannot be verified after the fact.
- `--storage-uri` — storage file path (defaults to the shared SQLite
  file; required for `ndjson`).
- `--check-determinism` — also re-run the business from its recorded
  seed and diff the streams. Runs whose inputs are not recoverable
  from storage (action audits, scenario markers, a cafe
  `shift_closed`) report this check as `SKIP` with the reason named.

The always-on `resume_integrity` check restores the run's final
checkpoint into a fresh simulator and proves it reproduces the
stored final position (storage keeps only the latest snapshot, so
earlier checkpoints cannot be proven after the fact — the check
states this limit in its own output; legacy summary-only snapshots
report `SKIP` with the reason).

Output is one `PASS`/`FAIL`/`SKIP` line per check (`sequence_integrity`,
`period_summaries`, `position`, `action_audit`, `resume_integrity`,
plus `determinism` when requested), naming the period, field or sequence involved in any
failure, followed by a `VERDICT:` line. Position fields that the event
stream does not determine (setup state from `initialize()`, tallies
whose transitions are not all named in the stream) are printed as
`UNVERIFIABLE` with the reason — they are never silently passed. The
exit code is 0 iff no check fails.

## Period KPI summaries

Runs also emit `period_summary` events: one per simulated calendar
day (revenue, order counts, rates — the KPIs declared in each
business's definition), closed at each simulated midnight. Batch
(`run`) runs add a final `"partial": true` summary for the last open
period at run end; daemon runs instead checkpoint the open period
in-progress, so a restarted daemon closes the day as a single
summary rather than splitting it at the restart.
They appear in the NDJSON stream / persisted storage alongside
business events and can be used to grade a run objectively: every
stream-based KPI can be recomputed from the raw events between the
summary's `sequence_start` and `sequence_end`. Disable them with
`--no-period-summaries` on the `run` and `daemon` commands.

## Actions (Phase 3 cafe pilot, Phase 4a gym + clinic, Phase 4b trades, Phase 4c ecommerce + saas + logistics)

Simulators can be *managed*, not just observed. The cafe accepts
four actions — `place_supplier_order`, `adjust_price` (requires an
`approved_by` stamp on the request), `clock_staff_in` and
`clock_staff_out` — declared in its business definition. The gym
accepts `freeze_membership`, `unfreeze_membership`, `schedule_class`,
`cancel_class`, `send_retention_outreach` and `adjust_plan_price`
(also approval-gated); the clinic accepts `schedule_appointment`,
`cancel_appointment`, `reschedule_appointment`, `add_provider_shift`
and `chase_claim`. The three trades businesses — `field_service`,
`electrician` and `plumber` — share one implementation and one
action catalogue: `assign_technician`, `dispatch_technician`,
`order_parts`, `approve_estimate`, `chase_invoice` and
`schedule_follow_up`, differentiated only by their configured
categories and parts catalogues (a part valid for one trade is
rejected as unknown by another). None of the trades actions
requires approval. With Phase 4c every business has a catalogue:
ecommerce accepts `adjust_price` and `issue_refund` (both
approval-gated), `restock_product` and `expedite_order`; saas
accepts `chase_invoice`, `change_plan_price` (approval-gated),
`freeze_account`, `unfreeze_account`, `offer_discount`
(approval-gated) and `resolve_ticket`; logistics accepts
`assign_driver`, `replan_route`, `expedite_shipment` and
`initiate_return`. Programmatic
callers submit `ActionRequest`s through a runner: scripted
`actions=[(sequence, request)]` for deterministic runs, or
`ContinuousRunner.submit_action(request)` from another thread.
Each applied action appears in the stream as its domain effect
event followed by an `action_applied` audit event; rejections
(invalid parameters, unknown entities, missing approval, duplicate
`action_id`) appear as `action_rejected` with a reason and change
nothing. See `docs/architecture.md` ("Action channel") for the full
contract.

## Replay stored events

`replay` reads a persisted stream and re-emits the events at original or scaled timing.

```bash
# Replay an entire stream at real time
uv run eden-business-simulator replay gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db --speed 1.0

# Replay from sequence 50 to 99 at 10x speed
uv run eden-business-simulator replay gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db \
  --from-sequence 50 --to-sequence 99 --speed 10.0
```

Replay flags:

| Flag | Description |
|------|-------------|
| `--from-sequence` | First logical sequence to replay (inclusive). |
| `--to-sequence` | Last logical sequence to replay (inclusive). |
| `--speed` | Multiplier applied to the original inter-event delay (`1.0` = real time). |
| `--output`, `-o` | `ndjson` (default) or `http`. |
| `--webhook-url` | Webhook URL when `--output http`. |

## Inspect streams and force checkpoints

```bash
# List streams, latest sequences, and checkpoint positions
uv run eden-business-simulator status --storage sqlite --storage-uri gym_demo.db

# Force a snapshot/checkpoint for an existing stream
uv run eden-business-simulator checkpoint gym gym_demo_seed42 \
  --storage sqlite --storage-uri gym_demo.db
```

`status` reports one line per stream:

```text
stream=gym_demo_seed42 latest_sequence=123 checkpoint=123
```

For `ndjson` storage, `status` requires `--storage-uri` because the adapter cannot enumerate files in a directory.

## Evaluator integration patterns

### 1. Pipe stdout into an evaluator

```bash
uv run eden-business-simulator run ecommerce --duration 60 --rate 5 --no-realtime | ./evaluator
```

### 2. Consume from storage

Run a daemon to persist events, then point an evaluator at the SQLite database or NDJSON file:

```bash
uv run eden-business-simulator daemon logistics \
  --stream-id logistics_run_1 \
  --storage sqlite --storage-uri runs.db \
  --duration 300 --rate 2 --no-realtime

# Evaluator reads directly from runs.db
```

### 3. HTTP webhook

```bash
uv run eden-business-simulator run clinic --duration 120 --rate 2 \
  --output http --webhook-url http://localhost:8080/events
```

Each event is POSTed as a JSON `EventEnvelope`.

### 4. Replay into an evaluator

```bash
uv run eden-business-simulator replay logistics_run_1 \
  --storage sqlite --storage-uri runs.db \
  --speed 100.0 | ./evaluator
```

Replay always reads stored events at wall-clock pacing scaled by `--speed`; there is no `--no-realtime` flag for replay. To make replay emit as fast as possible, use a large `--speed` value such as `100.0`.

## Determinism and seeds

Every simulator accepts `--seed`. With the same seed and `--no-realtime`, the event sequence is reproducible. Use a stable seed when you need:

- deterministic test fixtures,
- repeatable benchmark inputs, or
- A/B comparisons of evaluator behavior.

The seed initializes both Python's `random.Random` and `faker.Faker` instances inside each simulator. The framework helpers reuse those instances, so IDs, names, and payloads all derive from the same seed.

## Common `--stream-id` guidance

| Scenario | Recommendation |
|----------|---------------|
| One-off batch run | No stream ID needed; use `run`. |
| Deterministic replay | Pass `--stream-id <business>_seed<seed>` to `daemon` and `replay`. |
| Multiple parallel evaluators | Use a unique stream ID per evaluator. |
| Discover existing streams | Run `status --storage <backend> --storage-uri <uri>`. |

## Next steps

- Learn what each business domain emits: [`docs/businesses/`](businesses/).
- Understand the envelope and storage model: [`docs/architecture.md`](architecture.md).
- Add a new simulator: [`docs/adding_a_business.md`](adding_a_business.md).
