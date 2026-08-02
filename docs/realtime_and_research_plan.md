# Real-Time Persistence and Expanded Domain Research Plan

Goal: `28d3a301-185e-4610-bdf9-3f2577d616e0`

Deepen business and market research so generated data is abundant and realistic
across multiple business domains. Add a long-running process mode that generates
data in real time and persists it so the stream can be replayed later.

## Expanded business domain research

### 1. Field services / trades (`field_service`)

**Systems used:** dispatch boards, technician mobile apps, GPS/fleet tracking,
parts inventory, CRM, quoting/invoicing, payment terminals.

**Operational workflow:**
1. Ticket created from customer call, web form, IoT alert, or contract SLA.
2. Dispatcher assigns technician based on skills, location, and availability.
3. Technician travels to site, checks in, diagnoses issue.
4. Parts used or ordered; work performed; photos/notes captured.
5. Customer signs off; invoice generated; payment processed; follow-up scheduled.

**Event catalog (13 events):**

1. `service_ticket_created`
   ```json
   {"ticket_id":"tkt_0001","customer_id":"cust_0001","contract_id":null,
    "priority":"high","category":"hvac_repair","description":"AC not cooling",
    "created_at":"2026-08-02T07:12:00Z"}
   ```

2. `technician_assigned`
   ```json
   {"ticket_id":"tkt_0001","technician_id":"tech_0003","vehicle_id":"vh_0002",
    "assigned_at":"2026-08-02T07:15:00Z","estimated_arrival":"2026-08-02T08:30:00Z"}
   ```

3. `technician_dispatched`
   ```json
   {"ticket_id":"tkt_0001","technician_id":"tech_0003","dispatched_at":"2026-08-02T07:20:00Z",
    "location":{"lat":-33.8688,"lon":151.2093}}
   ```

4. `technician_arrived`
   ```json
   {"ticket_id":"tkt_0001","technician_id":"tech_0003","arrived_at":"2026-08-02T08:28:00Z",
    "location":{"lat":-33.8689,"lon":151.2094}}
   ```

5. `diagnosis_recorded`
   ```json
   {"ticket_id":"tkt_0001","technician_id":"tech_0003","fault_code":"F_ Refrigerant_Low",
    "notes":"Refrigerant pressure below spec","recorded_at":"2026-08-02T08:35:00Z"}
   ```

6. `parts_used`
   ```json
   {"ticket_id":"tkt_0001","part_id":"prt_001","part_name":"R410A refrigerant 1kg",
    "qty":1,"unit_cost":45.0,"used_at":"2026-08-02T08:45:00Z"}
   ```

7. `work_completed`
   ```json
   {"ticket_id":"tkt_0001","technician_id":"tech_0003","resolution":"Recharged refrigerant",
    "labor_minutes":45,"completed_at":"2026-08-02T09:20:00Z"}
   ```

8. `customer_sign_off`
   ```json
   {"ticket_id":"tkt_0001","customer_id":"cust_0001","signed_at":"2026-08-02T09:25:00Z",
    "signature_method":"mobile_pin","satisfaction_score":5}
   ```

9. `invoice_generated`
   ```json
   {"invoice_id":"inv_0001","ticket_id":"tkt_0001","customer_id":"cust_0001",
    "labor_amount":112.5,"parts_amount":45.0,"tax_amount":15.75,"total":173.25,
    "generated_at":"2026-08-02T09:30:00Z"}
   ```

10. `payment_received`
    ```json
    {"payment_id":"pay_0001","invoice_id":"inv_0001","amount":173.25,"method":"card",
     "status":"settled","received_at":"2026-08-02T09:31:00Z"}
    ```

11. `follow_up_scheduled`
    ```json
    {"ticket_id":"tkt_0001","follow_up_id":"fu_0001","follow_up_type":"maintenance_check",
     "scheduled_date":"2026-09-02","scheduled_at":"2026-08-02T09:35:00Z"}
    ```

12. `vehicle_location_update`
    ```json
    {"vehicle_id":"vh_0002","technician_id":"tech_0003","location":{"lat":-33.87,"lon":151.21},
     "speed_kmh":42,"recorded_at":"2026-08-02T08:15:00Z"}
    ```

13. `parts_reorder_placed`
    ```json
    {"reorder_id":"ro_0001","supplier_id":"sup_03","parts":[{"part_id":"prt_001","qty":10}],
     "placed_at":"2026-08-02T16:00:00Z"}
    ```

### 2. Healthcare clinic / general practice (`clinic`)

**Systems used:** EHR/EMR, practice management, appointment scheduling, patient
portal, lab interfaces, billing/claims, pharmacy, telehealth.

**Operational workflow:**
1. Appointment scheduled via phone, portal, or referral.
2. Patient checks in; demographics and insurance verified.
3. Vitals captured; clinician encounter; diagnosis/procedure; lab orders.
4. Lab results returned; medication prescribed; referral sent.
5. Claim submitted; payment posted; follow-up scheduled.

**Event catalog (14 events):**

1. `appointment_scheduled`
   ```json
   {"appointment_id":"appt_0001","patient_id":"pt_0001","provider_id":"dr_0002",
    "scheduled_at":"2026-08-02T08:00:00Z","appointment_time":"2026-08-03T09:30:00Z",
    "reason":"annual_physical","channel":"portal"}
   ```

2. `patient_checked_in`
   ```json
   {"appointment_id":"appt_0001","patient_id":"pt_0001","checked_in_at":"2026-08-03T09:28:00Z",
    "waiting_minutes":2,"self_reported_symptoms":["fatigue"]}
   ```

3. `vitals_recorded`
   ```json
   {"encounter_id":"enc_0001","patient_id":"pt_0001","height_cm":170,"weight_kg":72,
    "bp_systolic":128,"bp_diastolic":82,"heart_rate":68,"temperature_c":36.6,
    "recorded_at":"2026-08-03T09:35:00Z"}
   ```

4. `encounter_started`
   ```json
   {"encounter_id":"enc_0001","appointment_id":"appt_0001","patient_id":"pt_0001",
    "provider_id":"dr_0002","started_at":"2026-08-03T09:40:00Z"}
   ```

5. `diagnosis_recorded`
   ```json
   {"encounter_id":"enc_0001","patient_id":"pt_0001","icd10_code":"E11.9",
    "diagnosis":"Type 2 diabetes mellitus without complications",
    "severity":"chronic","recorded_at":"2026-08-03T09:55:00Z"}
   ```

6. `procedure_performed`
   ```json
   {"encounter_id":"enc_0001","procedure_code":"99395","description":"Annual physical",
    "provider_id":"dr_0002","performed_at":"2026-08-03T09:50:00Z"}
   ```

7. `lab_order_placed`
   ```json
   {"lab_order_id":"lo_0001","encounter_id":"enc_0001","patient_id":"pt_0001",
    "tests":[{"code":"80053","name":"Comprehensive metabolic panel"}],
    "lab_id":"lab_01","ordered_at":"2026-08-03T10:00:00Z"}
   ```

8. `lab_result_received`
   ```json
   {"lab_result_id":"lr_0001","lab_order_id":"lo_0001","patient_id":"pt_0001",
    "results":[{"test_code":"80053","value":"in_range","note":"All values normal"}],
    "received_at":"2026-08-04T08:15:00Z"}
   ```

9. `medication_prescribed`
   ```json
   {"prescription_id":"rx_0001","encounter_id":"enc_0001","patient_id":"pt_0001",
    "medication":"metformin_500mg","quantity":30,"refills":2,"pharmacy_id":"ph_01",
    "prescribed_at":"2026-08-03T10:05:00Z"}
   ```

10. `claim_submitted`
    ```json
    {"claim_id":"clm_0001","encounter_id":"enc_0001","patient_id":"pt_0001",
     "payer_id":"pay_01","amount":250.0,"submitted_at":"2026-08-03T10:30:00Z"}
    ```

11. `claim_adjudicated`
    ```json
    {"claim_id":"clm_0001","status":"paid","paid_amount":200.0,"patient_responsibility":50.0,
     "adjudicated_at":"2026-08-05T14:00:00Z"}
    ```

12. `payment_posted`
    ```json
    {"payment_id":"pay_0001","claim_id":"clm_0001","patient_id":"pt_0001",
     "amount":50.0,"method":"card","posted_at":"2026-08-06T09:00:00Z"}
    ```

13. `referral_sent`
    ```json
    {"referral_id":"ref_0001","encounter_id":"enc_0001","patient_id":"pt_0001",
     "to_provider_id":"dr_endo_01","reason":"endocrinology_consult","sent_at":"2026-08-03T10:10:00Z"}
    ```

14. `no_show_recorded`
    ```json
    {"appointment_id":"appt_0002","patient_id":"pt_0002","scheduled_time":"2026-08-03T11:00:00Z",
     "recorded_at":"2026-08-03T11:05:00Z"}
    ```

### 3. Logistics / last-mile delivery (`logistics`)

**Systems used:** route optimization, driver apps, GPS tracking, warehouse WMS,
proof-of-delivery scanners, customer notifications, feedback/returns portals.

**Operational workflow:**
1. Shipment created and routed; driver assigned; manifest printed.
2. Driver loads vehicle; depots/scans; follows optimized route.
3. Arrival at stop; delivery attempt; POD capture; exception if failed.
4. Customer feedback; return initiated if needed; route completion.

**Event catalog (13 events):**

1. `shipment_created`
   ```json
   {"shipment_id":"shp_0001","order_id":"ord_0001","destination":{"lat":-33.91,"lon":151.25},
    "service_level":"same_day","weight_kg":2.5,"created_at":"2026-08-02T06:00:00Z"}
   ```

2. `route_planned`
   ```json
   {"route_id":"rte_0001","driver_id":"drv_0001","vehicle_id":"vh_0001",
    "stops":[{"shipment_id":"shp_0001","sequence":1,"eta":"2026-08-02T09:15:00Z"}],
    "planned_at":"2026-08-02T07:30:00Z"}
   ```

3. `driver_assigned`
   ```json
   {"route_id":"rte_0001","driver_id":"drv_0001","vehicle_id":"vh_0001","assigned_at":"2026-08-02T07:35:00Z"}
   ```

4. `vehicle_departed`
   ```json
   {"route_id":"rte_0001","vehicle_id":"vh_0001","departed_at":"2026-08-02T08:00:00Z",
    "initial_odometer_km":45230}
   ```

5. `stop_arrived`
   ```json
   {"stop_id":"stp_0001","route_id":"rte_0001","shipment_id":"shp_0001",
    "arrived_at":"2026-08-02T09:12:00Z","location":{"lat":-33.91,"lon":151.25}}
   ```

6. `delivery_attempted`
   ```json
   {"stop_id":"stp_0001","shipment_id":"shp_0001","attempted_at":"2026-08-02T09:14:00Z",
    "outcome":"delivered"}
   ```

7. `proof_of_delivery_captured`
   ```json
   {"pod_id":"pod_0001","shipment_id":"shp_0001","recipient_name":"A. Customer",
    "signature_type":"photo","captured_at":"2026-08-02T09:15:00Z"}
   ```

8. `delivery_exception_recorded`
   ```json
   {"exception_id":"exc_0001","shipment_id":"shp_0002","stop_id":"stp_0002",
    "reason":"customer_not_home","resolution":"leave_with_neighbor",
    "recorded_at":"2026-08-02T10:20:00Z"}
   ```

9. `customer_feedback_received`
   ```json
   {"feedback_id":"fb_0001","shipment_id":"shp_0001","rating":5,"comment":"Fast delivery",
    "received_at":"2026-08-02T09:25:00Z"}
   ```

10. `return_initiated`
    ```json
    {"return_id":"ret_0001","shipment_id":"shp_0001","order_id":"ord_0001",
     "reason":"wrong_item","initiated_at":"2026-08-02T12:00:00Z"}
    ```

11. `vehicle_location_update`
    ```json
    {"vehicle_id":"vh_0001","driver_id":"drv_0001","location":{"lat":-33.905,"lon":151.24},
     "speed_kmh":38,"recorded_at":"2026-08-02T08:45:00Z"}
    ```

12. `route_completed`
    ```json
    {"route_id":"rte_0001","vehicle_id":"vh_0001","completed_at":"2026-08-02T17:30:00Z",
     "stops_completed":22,"stops_failed":1,"final_odometer_km":45380}
    ```

13. `fuel_stop_logged`
    ```json
    {"fuel_stop_id":"fs_0001","vehicle_id":"vh_0001","liters":42.5,"cost":75.0,
     "location":{"lat":-33.89,"lon":151.22},"logged_at":"2026-08-02T14:10:00Z"}
    ```

### 4. Gym / fitness studio (`gym`)

**Systems used:** membership CRM, access control, class booking, POS/retail,
personal training scheduling, attendance tracking, billing.

**Operational workflow:**
1. Member signs up; membership tier chosen; recurring billing set.
2. Check-in via card/app; class booking/cancel; PT session scheduled.
3. Workout logged; progress notes; retail purchase.
4. Failed payment; retention outreach; membership freeze/cancel.

**Event catalog (13 events):**

1. `membership_enrolled`
   ```json
   {"member_id":"mem_0001","membership_id":"mbr_0001","tier":"premium",
    "start_date":"2026-08-01","billing_frequency":"monthly","monthly_fee":79.0,
    "enrolled_at":"2026-08-01T10:00:00Z"}
   ```

2. `check_in_recorded`
   ```json
   {"check_in_id":"ci_0001","member_id":"mem_0001","location":"main_gym",
    "checked_in_at":"2026-08-02T06:45:00Z"}
   ```

3. `class_booked`
   ```json
   {"booking_id":"bk_0001","member_id":"mem_0001","class_id":"cls_yoga_07",
    "class_name":"Morning Yoga","instructor_id":"inst_0001",
    "scheduled_at":"2026-08-02T07:00:00Z","booked_at":"2026-08-01T20:00:00Z"}
   ```

4. `class_cancelled`
   ```json
   {"booking_id":"bk_0001","member_id":"mem_0001","cancelled_at":"2026-08-02T06:30:00Z",
    "reason":"late_cancel","penalty_applied":true}
   ```

5. `class_attended`
   ```json
   {"attendance_id":"att_0001","booking_id":"bk_0001","member_id":"mem_0001",
    "class_id":"cls_yoga_07","checked_in_at":"2026-08-02T06:58:00Z"}
   ```

6. `pt_session_scheduled`
   ```json
   {"session_id":"sess_0001","member_id":"mem_0001","trainer_id":"trn_0001",
    "session_type":"strength","scheduled_at":"2026-08-02T18:00:00Z","duration_minutes":60}
   ```

7. `workout_logged`
   ```json
   {"workout_id":"wo_0001","member_id":"mem_0001","checked_in_at":"2026-08-02T06:45:00Z",
    "exercises":[{"name":"bench_press","sets":3,"reps":10,"weight_kg":80}],
    "logged_at":"2026-08-02T08:00:00Z"}
   ```

8. `progress_recorded`
   ```json
   {"member_id":"mem_0001","measurement_type":"body_weight","value_kg":78.5,
    "recorded_at":"2026-08-02T08:05:00Z"}
   ```

9. `retail_purchase_made`
   ```json
   {"purchase_id":"pur_0001","member_id":"mem_0001","items":[{"sku":"PROTEIN_1KG","qty":1,"price":45.0}],
    "total":45.0,"payment_method":"card","purchased_at":"2026-08-02T08:10:00Z"}
   ```

10. `payment_failed`
    ```json
    {"member_id":"mem_0001","membership_id":"mbr_0001","amount":79.0,
     "failure_reason":"insufficient_funds","attempted_at":"2026-08-02T09:00:00Z"}
    ```

11. `retention_outreach_sent`
    ```json
    {"outreach_id":"out_0001","member_id":"mem_0001","channel":"email",
     "message_type":"win_back","sent_at":"2026-08-10T10:00:00Z"}
    ```

12. `membership_frozen`
    ```json
    {"member_id":"mem_0001","membership_id":"mbr_0001","frozen_at":"2026-08-15T09:00:00Z",
     "resume_date":"2026-09-15"}
    ```

13. `membership_cancelled`
    ```json
    {"member_id":"mem_0001","membership_id":"mbr_0001","cancelled_at":"2026-08-20T09:00:00Z",
     "reason":"relocated","churn_risk_score":0.9}
    ```

## Real-time persistence and replay architecture

### Goals

- Generate events continuously in wall-clock time.
- Persist every event durably so it can be replayed later.
- Support replay at original speed, scaled speed, or as-fast-as-possible.
- Resume from restart without duplicating events or losing state.
- Work locally by default; support optional remote backends.

### Storage backend options

| Backend | Pros | Cons | Default? |
|---|---|---|---|
| SQLite append-only log | Zero infra, transactional, WAL mode, queryable | Single-node, not distributed | **Default** |
| NDJSON/JSONL file append | Simple, human-readable, easy to tail | No querying, compaction harder | Optional |
| DuckDB / Parquet | Analytical, compact, time-series friendly | Slightly heavier dependency | Optional |
| Redis Streams | Pub/sub, replay offsets, TTL | Requires Redis running | Optional |
| Kafka | Distributed, high throughput | Heavy local setup | Optional |
| PostgreSQL / TimescaleDB | Robust, scalable | Requires Postgres | Optional |
| S3-compatible object store | Durable, cheap | Requires credentials/cloud | Optional |

**Decision:** SQLite is the default local backend. Additional backends are exposed
through a `StorageAdapter` interface, mirroring the existing `OutputAdapter` pattern.

### Storage schema (SQLite default)

Tables:

- `event_log` — every emitted event
  - `stream_id` TEXT (e.g., "cafe_seed42_20260802")
  - `sequence` INTEGER (monotonic per stream)
  - `timestamp` TEXT (ISO envelope timestamp)
  - `business_type` TEXT
  - `event_type` TEXT
  - `payload_json` TEXT
  - `event_id` TEXT
  - `recorded_at` TEXT (wall-clock insert time)
  - PK (`stream_id`, `sequence`)

- `snapshots` — periodic simulator state snapshots
  - `stream_id` TEXT
  - `sequence` INTEGER (last event sequence included)
  - `snapshot_json` TEXT
  - `timestamp` TEXT
  - PK (`stream_id`, `sequence`)

- `checkpoints` — consumer/evaluator offsets
  - `stream_id` TEXT
  - `consumer_id` TEXT
  - `last_sequence` INTEGER
  - `updated_at` TEXT

### Process model

- `ContinuousRunner` replaces the batch `Runner` for daemon mode.
- Runs in wall-clock time by default; optional simulated-time mode for deterministic replay.
- SIGTERM/SIGINT handler flushes pending events and writes a final snapshot.
- Periodic checkpointing every N events or M seconds.
- Supports pluggable `StorageAdapter` and `OutputAdapter` simultaneously.

### CLI commands

- `eden-business-simulator daemon <business_type>` — run continuously, persist events.
  Options: `--storage sqlite|redis|kafka|ndjson`, `--storage-uri`, `--checkpoint-interval`, `--stream-id`, `--seed`, `--rate`, `--state-file`.
- `eden-business-simulator replay <stream_id>` — replay stored events.
  Options: `--speed 1.0`, `--from-sequence`, `--to-sequence`, `--output ndjson|http`, `--loop`.
- `eden-business-simulator status` — show running streams, last sequence, storage health.
- `eden-business-simulator checkpoint <stream_id>` — force a snapshot.

### Resume semantics

- Each stream has a monotonic `sequence` per `stream_id`.
- On restart, `ContinuousRunner` reads the latest snapshot and resumes from the next sequence.
- Optional `BusinessSimulator.restore(snapshot)` hook for stateful domains.
- Deterministic seed + sequence guarantee reproducibility from a snapshot.

### Framework gaps and changes needed

1. **Runner** — add `ContinuousRunner` with wall-clock pacing, signal handling, and storage integration.
2. **Storage adapters** — add `StorageAdapter` base and SQLite/NDJSON implementations.
3. **BusinessSimulator** — add optional `restore(snapshot)` hook and `snapshot()` helper.
4. **EventEnvelope** — add `sequence` and `stream_id` fields.
5. **Config** — add storage, stream, checkpoint, and daemon options.
6. **CLI** — add `daemon`, `replay`, `status`, `checkpoint` subcommands.
7. **Models** — add `StreamConfig`, `Checkpoint`, `Snapshot` Pydantic models.
8. **Replay adapter** — read from storage and emit through existing output adapters.

## Implementation status

Implemented on branch `feature/realtime-persistence-and-domains`:

1. Storage adapter layer (`src/eden_business_simulator/storage/`):
   - `StorageAdapter` abstract base class and `StoredRecord` dataclass.
   - `SqliteStorageAdapter` with WAL mode, `event_log`, `snapshots`, and `checkpoints` tables.
   - `NdjsonStorageAdapter` append-only JSONL with companion metadata file.
   - `MemoryStorageAdapter` for unit tests.
2. Model and config extensions:
   - `EventEnvelope` now carries optional `stream_id` and `sequence`.
   - Added `StreamConfig`, `Snapshot`, and `Checkpoint` Pydantic models.
   - `SimulatorConfig` extended with `storage_backend`, `storage_uri`, `stream_id`, and checkpoint intervals.
3. `BusinessSimulator.restore(snapshot)` optional hook added; default is a safe no-op.
4. `ContinuousRunner` (`src/eden_business_simulator/continuous_runner.py`) with wall-clock pacing, SIGTERM/SIGINT handling, periodic snapshots/checkpoints, and resume from latest checkpoint + next sequence.
5. CLI commands:
   - `daemon <business_type>` — continuous generation with persistence.
   - `replay <stream_id>` — replay stored events at original or scaled speed.
   - `status` — list streams and latest sequence (SQLite only).
   - `checkpoint <business_type> <stream_id>` — force a snapshot.
6. New business domains:
   - `gym` — 14 event types covering memberships, check-ins, classes, PT, workouts, progress, retail, billing, retention, churn.
   - `logistics` — 13 event types covering shipments, routes, drivers, stops, delivery attempts, POD, exceptions, feedback, returns, fuel.
   - `field_service` — 13 event types covering service tickets, technician dispatch/arrival, diagnosis, parts, work completion, invoicing, payment, follow-up, vehicle tracking.
   - `clinic` — 14 event types covering appointments, check-in, vitals, encounters, diagnosis, procedures, labs, prescriptions, claims, payments, referrals, no-shows.
7. Documentation updated:
   - `README.md` with daemon/replay/status/checkpoint examples and new business types.
   - `docs/adding_a_business.md` with storage/replay notes and the `restore` hook.
   - `docs/realtime_and_research_plan.md` with this implementation status section.

## Rework after Verifier red verdict

Goal `28d3a301-185e-4610-bdf9-3f2577d616e0` was handed back to Builder because the
Verifier found three CLI/storage defects in the replay/status area
(verdict `12b66733-524f-4fba-996f-3f30f3eebc5d`).

### Defects fixed

1. **`replay --from-sequence` semantics were broken.**
   - The CLI passed the sequence value as a storage `offset`, but `offset` means
     SQLite `rowid` and NDJSON byte offset.
   - `StorageAdapter.read_from` now accepts a separate `from_sequence` argument.
   - SQLite filters by `sequence >= ?`; NDJSON filters by `record.sequence >= ?`;
     `MemoryStorageAdapter` filters by sequence.
   - CLI `replay` now calls `read_from(from_sequence=...)`.

2. **`replay --speed` was ignored.**
   - Replay now paces events by the wall-clock delta between envelope timestamps,
     divided by `--speed`.
   - `--speed 1.0` replays at original timing; values >1 are faster, values <1 are
     slower. Values <=0 are rejected.

3. **`status` showed `checkpoint=none` for every stream.**
   - The command used a single adapter with an empty `stream_id`, so
     `read_checkpoint()` could never match a per-stream checkpoint.
   - `status` now loads the storage adapter, calls `stream_ids()` to enumerate
     distinct streams, then instantiates a per-stream adapter to read the correct
     checkpoint.
   - `stream_ids()` is implemented for SQLite and NDJSON.

### Additional improvements during rework

- Added `--duration` to `daemon` so bounded runs can stop after a simulated-time
  duration as well as after `--max-events`. This makes the adversarial
  verification command (`daemon gym --duration 5 ...`) work as written.
- Fixed `NDJsonOutputAdapter` to resolve `sys.stdout` lazily at instantiation time
  instead of binding the default at function-definition time. This lets the
  Typer test runner capture replayed NDJSON output correctly.

### Test results after rework

`uv run pytest` passes with 110 tests.

### Verification commands run

- `eden-business-simulator run cafe --duration 0.5 --rate 10 --seed 42 --no-realtime` — OK.
- `eden-business-simulator daemon gym --duration 5 --rate 2 --seed 1 --storage sqlite --storage-uri /tmp/gym_rework.db --no-realtime`, then `replay ... --from-sequence 2` — sequences start at 2 — OK.
- Same daemon/replay flow with `--storage ndjson` — sequences start at 2 — OK.
- `eden-business-simulator status --storage sqlite --storage-uri /tmp/gym_rework.db` — shows `stream=... latest_sequence=9 checkpoint=9` — OK.

## Deviations from the original plan

- `output_mode` was extended to include `none` so `daemon` can persist without writing to stdout.
- `daemon` accepts `--max-events` to make automated testing and bounded runs practical; the original spec implied infinite runs controlled only by signals.
- `daemon` now also accepts `--duration` for bounded simulated-time runs.
- `status` now supports both SQLite and NDJSON backends.

## Test results

`uv run pytest` passes with 110 tests.

## Sources

- Field service management: FieldCamp (https://fieldcamp.ai/features/work-order-management/),
  Navixy FSM (https://navixy.com/en/field-service/solutions/field-service/),
  ArcRelay (https://www.arcrelayworks.com/)
- Healthcare EHR/practice management: DrChrono (https://www.drchrono.com/general-practice/),
  Elation Health (https://www.elationhealth.com/solutions/unified-ehr/),
  eCareHealth (https://www.ecarehealth.com/solutions/ehr-software/)
- Logistics and delivery: Beans Route (https://www.beansroute.ai/),
  Locus logistics (https://locus.sh/delivery-logistics-software/),
  Locus optimization (https://locus.sh/delivery-optimization-software/),
  Locate2u (https://www.locate2u.com/products/delivery-management/)
- Event storage patterns: SideshowDB spec (https://github.com/sideshowdb/sideshowdb/blob/main/docs/development/specs/sideshowdb-spec.md),
  mattbishop/sql-event-store (https://github.com/mattbishop/sql-event-store/blob/main/README.md),
  SQLite WAL (https://sqlite.org/wal.html)
