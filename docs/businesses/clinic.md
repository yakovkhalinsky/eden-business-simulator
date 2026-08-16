# Clinic simulator

Slug: `clinic`  
Source module: [`src/eden_business_simulator/businesses/clinic.py`](../../src/eden_business_simulator/businesses/clinic.py)

## Domain overview

The clinic simulator models outpatient healthcare workflows. Patients schedule appointments, check in, have vitals recorded, start encounters, receive diagnoses and procedures, get lab orders and results, receive prescriptions, submit insurance claims, see claim adjudications, post payments, and receive referrals. No-show appointments are also recorded.

## Systems represented

- Appointment scheduling and patient portal
- Electronic health record (EHR) encounters
- Vitals capture
- Diagnosis and procedure coding (ICD-10 / CPT)
- Lab orders and results
- ePrescribing
- Insurance claims and adjudication
- Patient payments
- Referrals
- No-show tracking

## Operational workflow

1. Seed providers, patients, payers, and medications.
2. Appointments are scheduled through the portal or by phone.
3. Patients check in and vitals are recorded.
4. Encounters start, diagnoses and procedures are recorded.
5. Providers order labs and receive results.
6. Medications are prescribed.
7. Claims are submitted, adjudicated, and paid.
8. Referrals are sent and no-shows are logged.

## Event catalog

### `appointment_scheduled`

```json
{
  "appointment_id": "appt_0001",
  "patient_id": "patient_0001",
  "provider_id": "provider_0002",
  "appointment_type": "sick_visit",
  "scheduled_at": "2026-08-02T13:21:17.630916+00:00",
  "appointment_time": "2026-08-03T05:21:17.630916+00:00",
  "reason": "follow_up",
  "channel": "patient_portal"
}
```

### `appointment_cancelled`

```json
{
  "appointment_id": "appt_0004",
  "patient_id": "patient_0004",
  "provider_id": "provider_0001",
  "reason": "patient_request",
  "cancelled_at": "2026-08-02T13:21:18.130916+00:00"
}
```

### `appointment_rescheduled`

```json
{
  "appointment_id": "appt_0002",
  "patient_id": "patient_0002",
  "provider_id": "provider_0003",
  "new_appointment_time": "2026-08-04T10:21:17.630916+00:00",
  "reason": "provider_unavailable",
  "rescheduled_at": "2026-08-02T13:21:19.230916+00:00"
}
```

### `patient_checked_in`

```json
{
  "appointment_id": "appt_0001",
  "patient_id": "patient_0001",
  "checked_in_at": "2026-08-02T13:21:17.730916+00:00",
  "waiting_minutes": 3,
  "self_reported_symptoms": []
}
```

### `vitals_recorded`

```json
{
  "encounter_id": "enc_0003",
  "patient_id": "patient_0001",
  "height_cm": 162,
  "weight_kg": 65.5,
  "bp_systolic": 126,
  "bp_diastolic": 93,
  "heart_rate": 87,
  "temperature_c": 37.5,
  "recorded_at": "2026-08-02T13:21:20.930916+00:00"
}
```

### `encounter_started`

```json
{
  "encounter_id": "enc_0001",
  "appointment_id": "appt_0001",
  "patient_id": "patient_0001",
  "provider_id": "provider_0002",
  "started_at": "2026-08-02T13:21:17.830916+00:00",
  "chief_complaint": "cough"
}
```

### `diagnosis_recorded`

```json
{
  "encounter_id": "enc_0001",
  "patient_id": "patient_0001",
  "icd10_code": "Z00.00",
  "diagnosis": "routine_health_exam",
  "severity": "chronic",
  "recorded_at": "2026-08-02T13:21:18.030916+00:00"
}
```

### `procedure_performed`

```json
{
  "encounter_id": "enc_0001",
  "procedure_code": "99395",
  "description": "annual_physical",
  "provider_id": "provider_0002",
  "performed_at": "2026-08-02T13:21:17.930916+00:00"
}
```

### `lab_order_placed`

```json
{
  "lab_order_id": "lo_0003",
  "encounter_id": "enc_0001",
  "patient_id": "patient_0001",
  "tests": [
    {
      "code": "lipid_panel",
      "name": "lipid panel"
    },
    {
      "code": "A1C",
      "name": "A1C"
    }
  ],
  "lab_id": "lab_quest",
  "ordered_at": "2026-08-02T13:21:19.630916+00:00"
}
```

### `lab_result_received`

```json
{
  "lab_result_id": "lr_0001",
  "lab_order_id": "lo_0001",
  "patient_id": "patient_0001",
  "results": [
    {
      "code": "WBC",
      "value": 5.6,
      "unit": "10^9/L",
      "flag": "normal"
    }
  ],
  "received_at": "2026-08-02T13:21:18.330916+00:00"
}
```

### `medication_prescribed`

```json
{
  "prescription_id": "rx_0001",
  "encounter_id": "enc_0001",
  "patient_id": "patient_0001",
  "medication": "metformin_500mg",
  "quantity": 49,
  "refills": 3,
  "pharmacy_id": "ph_01",
  "prescribed_at": "2026-08-02T13:21:19.330916+00:00"
}
```

### `claim_submitted`

```json
{
  "claim_id": "clm_0001",
  "encounter_id": "enc_0001",
  "patient_id": "patient_0001",
  "payer_id": "ins_0001",
  "diagnosis_codes": [
    "J06.9"
  ],
  "procedure_codes": [
    "99395"
  ],
  "billed_amount": 175.84,
  "tax_amount": 17.58,
  "tax_type": "GST",
  "submitted_at": "2026-08-02T13:21:18.730916+00:00"
}
```

### `claim_adjudicated`

```json
{
  "claim_id": "clm_0001",
  "status": "denied",
  "paid_amount": 0.0,
  "patient_responsibility": 175.84,
  "tax_amount": 17.58,
  "tax_type": "GST",
  "adjudicated_at": "2026-08-02T13:21:19.230916+00:00"
}
```

### `payment_posted`

```json
{
  "payment_id": "pmt_0001",
  "claim_id": "clm_0001",
  "patient_id": "patient_0001",
  "amount": 95.28,
  "tax_amount": 9.53,
  "tax_type": "GST",
  "method": "cash",
  "posted_at": "2026-08-02T13:21:20.030916+00:00"
}
```

### `referral_sent`

```json
{
  "referral_id": "ref_0001",
  "encounter_id": "enc_0001",
  "patient_id": "patient_0001",
  "to_provider_id": "spec_0001",
  "reason": "orthopedics",
  "sent_at": "2026-08-02T13:21:18.630916+00:00"
}
```

### `no_show_recorded`

```json
{
  "appointment_id": "appt_0004",
  "patient_id": "patient_0004",
  "scheduled_time": "2026-08-02T16:21:18.430916+00:00",
  "recorded_at": "2026-08-02T13:21:19.530916+00:00"
}
```

## Configuration notes

- Override initial counts with `initial_state_overrides`:
  - `initial_providers` (default `3`)
  - `initial_patients` (default `6`)
- Appointment types: `new_patient_visit`, `annual_physical`, `follow_up`, `sick_visit`.
- Channels: `patient_portal`, `phone`, `walk_in`.
- ICD-10 and CPT codes are sampled from small static catalogs.
- `lab_result_received.lab_order_id` now references a prior `lab_order_placed` event from the same stream.

## CLI quick start

```bash
uv run eden-business-simulator run clinic --duration 300 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon clinic \
  --stream-id clinic_seed42 \
  --storage sqlite --storage-uri clinic.db \
  --duration 600 --rate 2 --no-realtime

uv run eden-business-simulator replay clinic_seed42 \
  --storage sqlite --storage-uri clinic.db --speed 1.0
```
