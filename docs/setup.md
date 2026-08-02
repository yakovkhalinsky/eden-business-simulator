# Setup and runbook

This guide covers installing the Eden Business Simulator, generating event streams, and integrating the output into evaluator workflows.

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
| `--output`, `-o` | `ndjson` (default), `http`, or `none`. |
| `--webhook-url`, `-w` | Webhook URL when `--output http`. |

The default `ndjson` output writes one JSON `EventEnvelope` per line to stdout, so it is easy to pipe into another process.

```bash
uv run eden-business-simulator run gym --duration 120 --rate 2 --no-realtime | ./my-evaluator
```

### Output modes

- `ndjson` — one JSON event envelope per line to stdout.
- `http` — POST each event envelope as JSON to `--webhook-url`.
- `none` — suppress stdout output. This is mainly useful with `daemon` when only persistence matters.

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
