# Daemon command input — the operator contract (Phase 5)

A running daemon does not just emit events: it can be *operated*.
External operators — in the trial architecture, **Beth** (the
ops/approval layer) submitting on behalf of Aleph agents and the
owner — write **commands** into the run's storage. The daemon polls
the command table between ticks, applies each command through the
normal action pipeline (Phases 3–4), and records the outcome back on
the command record. The effect and audit events land in the event
stream exactly as if the action had been submitted live, so the
stream remains the single source of truth for what happened.

This page is the integration contract: how to submit, the record
shape, the status lifecycle, the approval rule, and idempotency.

## Submitting a command

### Via the CLI

```bash
uv run eden-business-simulator command cafe \
  --run-id cafe_demo_seed42 \
  --type place_supplier_order \
  --params '{"ingredient_id": "ing_001", "qty": 5}' \
  --requested-by beth \
  --storage sqlite --storage-uri cafe_demo.db
# -> command=cmd_000001 run_id=cafe_demo_seed42 action_id=cmd_000001 status=pending
```

`--run-id` is the daemon's `--stream-id`. Storage selection follows
the `position` verb's conventions: `--storage` is `sqlite` (default),
`ndjson`, or `memory`; `--storage-uri` defaults to
`eden_business_simulator.db` for SQLite and is required for NDJSON
(point it at the daemon's events file). `--params` must be a JSON
object; invalid JSON or an unknown business type is a clean error
with a non-zero exit.

Approval-gated actions (price changes, refunds, discounts — anything
whose definition sets `requires_approval`) need the approval stamp
at submission time:

```bash
uv run eden-business-simulator command cafe \
  --run-id cafe_demo_seed42 \
  --type adjust_price \
  --params '{"item_id": "item_0001", "new_price": 9.0}' \
  --requested-by aleph-run-123 \
  --approved-by yakov \
  --storage sqlite --storage-uri cafe_demo.db
```

### Via the storage API

Any process that can open the run's storage can submit directly:

```python
command_id = storage.submit_command(
    run_id,                      # = the daemon's stream id
    "place_supplier_order",
    {"ingredient_id": "ing_001", "qty": 5},
    requested_by="beth",
    approved_by=None,            # or the approver's stamp
    action_id=None,              # idempotency key; defaults to the command id
    submitted_at="2026-01-01T00:00:00+00:00",  # caller-supplied ISO string
)
```

Storage stays clock-free for commands: the caller supplies
`submitted_at`. Backends implement identical semantics:

| Backend | Where commands live |
|---------|--------------------|
| `sqlite` | A `commands` table in the same database file as the event log. |
| `ndjson` | A sibling append-only log, `<name>.commands.jsonl` next to the events file `<name>.jsonl`: one `submit` line per command, one `resolution` line per outcome; reads fold the two line kinds into final state. |
| `memory` | Dicts in the adapter instance — same process only, so CLI-to-daemon submission needs a durable backend. |

Because SQLite (WAL) and NDJSON reads go back to the file on every
poll, a command submitted by a separate CLI process is visible to a
long-running daemon on its next tick.

## The command record

```json
{
  "command_id": "cmd_000001",
  "run_id": "cafe_demo_seed42",
  "action_id": "cmd_000001",
  "action_type": "place_supplier_order",
  "params": {"ingredient_id": "ing_001", "qty": 5},
  "requested_by": "beth",
  "approved_by": null,
  "status": "pending",
  "submitted_at": "2026-01-01T00:00:00+00:00",
  "result": null,
  "resolved_at": null
}
```

- `command_id` is a per-run counter — `cmd_` plus a zero-padded
  count derived from the stored rows (`cmd_000001`, `cmd_000002`,
  ...). It is never wall-clock or random, so replaying the stored
  command log re-derives the same ids.
- `action_id` is the action pipeline's idempotency key. It defaults
  to the `command_id`; a caller retrying an operation should pass a
  stable `--action-id` of its own (see *Idempotency* below).

## Status lifecycle

```
pending ──daemon applies──> applied    (result: {"status": "applied",
             │                          "effect_event_types": [...]})
             └──daemon rejects──> rejected (result: {"status": "rejected"
                                            or "already_applied",
                                            "reason": "<pipeline reason>",
                                            "effect_event_types": []})
```

- The daemon drains **all** pending commands for its run each loop
  iteration, in submission order, between ticks (after any period
  close, before the next business event — the same D6 slot as
  scripted and live actions).
- Application goes through the shared `apply_action` pipeline:
  unknown action → duplicate id → approval gate → parameter
  validation → effect handler. The effect event(s) and the
  `action_applied` / `action_rejected` audit event are persisted and
  output like any other event; the command record is then resolved
  with the outcome.
- Rejection reasons are the pipeline's: `requires_approval`,
  `duplicate_action_id`, `unknown_action`, `missing_param:<name>`,
  `param_out_of_range:<name>`, domain reasons such as
  `unknown_ingredient:ing_999`, and so on.

Inspect records with the `commands` verb:

```bash
uv run eden-business-simulator commands cafe \
  --run-id cafe_demo_seed42 \
  --storage sqlite --storage-uri cafe_demo.db
# cmd_000001 action_id=cmd_000001 type=place_supplier_order status=applied
# cmd_000002 action_id=op-9 type=adjust_price status=rejected reason=requires_approval
```

Add `--status pending|applied|rejected` to filter.

## The approval rule (D9)

Beth owns approval; Eden only checks the stamp. If an action's
definition sets `requires_approval`, a command without `approved_by`
is rejected with reason `requires_approval` and nothing is mutated.
Eden does not know who *should* approve — recording who did
(`approved_by`, plus `requested_by` as the audit actor) is the whole
of its role.

## Idempotency across restarts

Applied action ids persist two ways, and the daemon seeds its
in-memory applied set from their union at startup:

1. **Applied command records** — every command resolved `applied`
   contributes its `action_id`.
2. **Stored `action_applied` events** — covers actions applied by
   scripted or live runners in earlier processes, which have no
   command record.

So if a daemon applies a command with `--action-id op-1`, stops, and
a restarted daemon later receives another command reusing
`op-1`, the second is rejected with `duplicate_action_id` and has no
effect — no double stock, no double charge. A caller that wants a
genuinely new operation uses a new `action_id`.

## Restart and resume

A daemon can be stopped (SIGTERM/SIGINT writes a final checkpoint)
and restarted against the same `--stream-id` and storage, and the
business *continues* — this is the restorable-state project
(S1–S4), and it is stronger than the idempotency guarantee above:

- **Full business state** — every checkpoint carries the business's
  complete state (entities, counters, in-flight work), the
  RNG/Faker/IdGenerator state, and the **simulated clock**, so the
  resumed stream is identical to the one an uninterrupted process
  would have produced: same events, same position, same sim time
  (dayparts and period boundaries do not restart).
- **The KPI tracker's open period** rides in the checkpoint too: a
  restart mid-day does not split that day's `period_summary` — the
  day closes once, as one summary. Operator note (R3): the
  continuous runner therefore no longer emits a final
  `"partial": true` summary when a process stops; the still-open
  day is summarised when a later day closes it (or a resume
  continues it). If you stop a daemon for good, its last partial
  day has no summary — read `eden position` for where it stands.
- **Scenario state** — pending scenarios still fire at their
  original offsets; an in-flight scenario ends at its original time
  and does not re-emit `scenario_started`.
- **Legacy checkpoints (R2)** — a stream whose last checkpoint
  predates full-state snapshots (summary-only) still resumes the
  old way: the sequence continues and applied action ids are still
  seeded (no double-apply), but business state and the sim clock
  restart. The daemon says so loudly at startup — a
  `resume_warnings` entry, a log warning and a `UserWarning` naming
  the stream — and the next checkpoint rewrites the snapshot in the
  current format. A snapshot whose format version this build cannot
  read is refused outright, never misread.
- Verify it afterwards: `eden verify`'s `resume_integrity` check
  restores the stored run's final checkpoint into a fresh simulator
  and proves it reproduces the stored final position (storage keeps
  only the latest snapshot, so earlier checkpoints cannot be proven
  after the fact — the check says exactly this).

## Honest limitations

- **Resume is genuine, but checkpoints are full-state snapshots**:
  they grow with the business's entity lists. Accepted for trial
  scale (R5); on very long runs the snapshot size in storage is the
  thing to watch.
- The `memory` backend is per-process: a CLI submission and a daemon
  must share one process to see each other's commands. Use SQLite or
  NDJSON for real operation.
- Command ids are allocated by counting stored rows; two processes
  submitting to the same run at the exact same moment could race.
  In the intended topology only the operator layer submits, one
  command at a time.
