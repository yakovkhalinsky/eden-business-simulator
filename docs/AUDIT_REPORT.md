# Audit Report

## Phase 8 addendum — the improvements programme (Phases 0–8)

This addendum summarises the programme that turned Eden from an
emit-only event generator into the trial world for Beth/Aleph-managed
businesses: Eden plays the business, Aleph agents manage it, Beth is
the ops/approval layer. Each phase landed with its test suite green
and is described in detail in the phase notes across `docs/`
(architecture, daemon, scenarios, setup).

### What was built, per phase

- **Phase 0 — Configuration foundation.** Every business is defined by
  a validated JSON + Pydantic `BusinessDefinition` (catalogues,
  dayparts, domain data, guard registry), with a definition hash
  recorded per run. Ecommerce and SaaS were normalised onto the shared
  weighted catalogue; the other seven businesses' golden streams are
  payload-identical to before.
- **Phase 1 — State read API.** `position_snapshot()` for all nine
  businesses (versioned envelope), stored by the daemon at each
  checkpoint under its own key and readable via `eden position`;
  opt-in in-stream `state_snapshot_recorded` events.
- **Phase 2 — KPI period summaries.** Config-declared KPIs
  (`count`/`count_where`/`sum`/`sum_where`/`ratio`/`snapshot`) emitted
  as `period_summary` events per simulated calendar day, plus a final
  partial summary. Observer/Advisor grading became possible: every
  stream-based KPI recomputes exactly from the raw events.
- **Phase 3 — Action channel (cafe pilot).** A shared `apply_action`
  pipeline — unknown action → duplicate → approval gate → parameter
  validation → handler — with effect events emitted before the
  `action_applied`/`action_rejected` audit event, and idempotency by
  `action_id`.
- **Phase 4 — Action catalogues for all nine businesses** (47 actions):
  gym + clinic (4a); the three trades sharing one implementation on
  the field-service base, differentiated only by config (4b);
  ecommerce + SaaS + logistics (4c). Price changes, refunds and
  discounts are flagged `requires_approval`.
- **Phase 5 — Daemon command input.** A commands table on all three
  storage backends; the daemon polls it between ticks and resolves
  each record applied/rejected with the reason. Beth owns approval;
  Eden only checks the `approved_by` stamp. Applied action ids persist
  in storage, so idempotency survives a daemon restart. See
  `docs/daemon.md` for the contract.
- **Phase 6 — Scenario packs.** Five config-driven disruption packs
  (supplier failure, staff sick day, demand spike, churn wave, payment
  failure wave) implemented as weight multipliers at the single
  catalogue choke point, with `scenario_started`/`scenario_ended`
  markers and a derived per-scenario RNG stream (D10). See
  `docs/scenarios.md`.
- **Phase 7 — Output hardening.** Batched HTTP output, bearer auth
  from the `EDEN_WEBHOOK_TOKEN` environment variable only (D11),
  bounded retry with backoff, and a dead-letter file: a dead consumer
  never raises into or crashes a run.
- **Phase 8 — Verifier + trial readiness.** `eden verify` (logic in
  `framework/verify.py`) recomputes a stored run independently —
  sequence integrity, period-summary KPIs, stream-derivable position
  figures, action-audit consistency, and an optional determinism
  re-run — plus scripted Observer/Advisor/Operator trial checks in
  `tests/test_trial_e2e.py`, including a corruption test proving the
  verifier catches stream drift and names the drifted KPI.

### Guarantees now held

- **Config-driven definitions**: adding or retuning a business,
  KPI, action or scenario pack is a JSON change validated against the
  schema, with the definition hash recorded on every period summary.
- **Position/KPI reconciliation**: for every business, the money
  totals, open-invoice sets and status tallies listed in
  `framework/verify.py` recompute exactly from the raw stream, and the
  Phase 8 verifier proves it on stored runs (419 → 453 tests,
  including per-business verifier passes over all nine).
- **Action audit + approval + restart idempotency**: every applied
  action is preceded by its effect events and followed by an audit
  naming them; approval-gated actions are refused without a stamp;
  a restarted daemon cannot double-apply an action id.
- **Scenario determinism**: a run with no scenario scheduled is
  byte-identical to the pre-Phase-6 golden streams; the same seed +
  schedule reproduces the same stream.
- **Delivery hardening**: HTTP consumers get auth, bounded retries
  and dead-letter capture; delivery failure never stops a run.

## Restorable-state addendum (S1–S5)

The Phase 8 programme closed with one carried limitation: a resumed
daemon restarted its business state, because checkpoints held only
summaries. The restorable-state project removed it, in five slices,
each proven by the split-run pattern — run N events, checkpoint,
resume into a fresh process/simulator, run M more; the split stream
and position must equal an uninterrupted run's exactly.

- **S1 — Framework machinery.** A versioned full-state section in the
  checkpoint envelope (`snapshot_format` 2): each business's complete
  state with datetimes explicitly encoded, plus the framework-owned
  RNG state, Faker state and IdGenerator counters — raw JSON (R6;
  pickle was rejected outright). The simulated clock rides in the
  envelope too. Legacy summary-only snapshots resume the old way
  (sequence continues, business state restarts) but now say so with
  a loud warning at startup (R2); an unrecognised format version is
  refused, never misread.
- **S2 — Trades + cafe.** Full-state snapshot/restore for the
  field-service base (hence electrician and plumber) and the cafe —
  the state-heaviest business, including its transition-model
  internals, inventory ledger, pending setup queue and
  action-mutated menu prices.
- **S3 — The remaining five.** Clinic, ecommerce, gym, logistics and
  SaaS, including action-mutated plan prices (gym/SaaS `_PLANS` are
  captured, not rebuilt from the definition) and the real datetimes
  stored inside entities. Gym's pre-existing vestigial `restore()`
  (it restored a key its snapshot never emitted) was replaced by the
  real thing.
- **S4 — Framework state resume (R3/R4).** The KPI tracker's open
  period and the scenario engine's pending/active state ride in the
  same envelope: a restart mid-day no longer splits that day's
  period summary, and an in-flight scenario ends at its original
  time without a duplicate `scenario_started`. Trade-off (R3): the
  continuous runner no longer emits a final `"partial": true` period
  summary at process end — the open period is checkpointed instead,
  and summarised when a later day closes it (or a resume continues
  it). The batch runner still flushes a final partial summary.
- **S5 — Verifier extension.** `eden verify` gained a
  `resume_integrity` check: the stored run's final checkpoint is
  restored into a fresh simulator (in the runner's restore order)
  and must re-serialise to the identical full state, reproduce the
  stored final position exactly, round-trip its tracker/scenario
  sections, and generate continuation events without error. Storage
  retains only the latest snapshot, so the check proves the *final*
  checkpoint is a genuine continuation point — it does not, and
  cannot, prove anything about earlier overwritten checkpoints, nor
  that the stored stream was in fact produced by resuming; the
  stream checks cover the stream itself. S5 also reclassified the
  gym member tallies (`members_by_status`, `active_count`,
  `frozen_count`, `cancelled_count`) as unverifiable-by-design: the
  member pool is seeded at `initialize()` before the stream starts,
  so a stream tally only coincides with the position when every
  seeded member happens to be touched by a status event.

### Guarantee added by the restorable-state project

- **Genuine resume**: for all nine businesses, a split run (stop,
  checkpoint, resume in a fresh process) is stream- and
  position-identical to an uninterrupted run — business state,
  RNG/Faker/IdGenerator state, simulated clock, the KPI tracker's
  open period and the scenario engine included. The final checkpoint
  of a stored run is independently provable as a continuation point
  via `eden verify`'s `resume_integrity` check.

### Known limitations (honest list)

- **State restore on resume is no longer a limitation** — see the
  restorable-state addendum above. What remains resume-adjacent:
  pre-S1 legacy snapshots still resume summary-style (with the R2
  warning), and checkpoint snapshots grow with the business's entity
  lists (accepted for trial scale, R5; snapshot size is the thing to
  watch on very long runs).
- **Scenario multipliers key on event types only.** Payment failures
  that are payload outcomes rather than event types (ecommerce
  `payment_processed`, clinic `claim_adjudicated`) are not covered by
  the payment-failure wave; the covered businesses are SaaS and gym.
- **The cafe setup queue bypasses weighted selection.** The opening
  shift's clock-ins and deliveries are emitted deterministically, so
  `staff_sick_day` and the delivery side of `supplier_failure` barely
  bite the cafe's first shift. State-mutating scenarios are future
  work.
- **Some position fields are not stream-derivable.** Setup state
  created at `initialize()` (gym classes/bookings **and the member
  pool tallies** — `members_by_status`, `active_count`,
  `frozen_count`, `cancelled_count`, reclassified in S5 — SaaS seed
  accounts and subscriptions/MRR, ecommerce opening stock, clinic
  patient and provider pools, trades technician counts) and tallies
  whose transitions are not all named in the stream (cafe
  order/ticket status, logistics shipment retention) cannot be
  recomputed by the verifier. `eden verify` lists them as
  `UNVERIFIABLE` with reasons rather than passing them silently.
- **Snapshot/computed KPIs are not stream-recomputable** (e.g. SaaS
  `mrr_end`): they record live position at period close by design and
  are reported as such by the verifier.
- **Determinism verification is shape-limited.** The original
  `SimulatorConfig` is not stored, so `--check-determinism` SKIPs —
  with the reason — runs containing action audits, scenario markers
  or a cafe `shift_closed`, whose inputs cannot be recovered from
  stored metadata. (The S5 `resume_integrity` check covers those
  runs' *checkpoints* instead — a different, weaker guarantee, and
  it says so in its own output.)
- **The memory storage backend is per-process**: its runs, commands
  and positions are not addressable after the process exits, and it
  cannot be verified.
- **Command counters assume a single submitting process at a time**
  per run (NDJSON/SQLite command ids are derived from stored rows;
  concurrent submitters could race the counter).
- **Deployment note**: environments whose injected proxy variables
  contain bracketed `[::1]` `no_proxy` entries break this httpx
  version's default client construction, so `--output http` fails in
  such an environment (observed in the development VM; not on the
  deployment host). Eden does not override the process proxy
  environment.
