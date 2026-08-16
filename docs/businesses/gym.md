# Gym simulator

Slug: `gym`  
Source module: [`src/eden_business_simulator/businesses/gym.py`](../../src/eden_business_simulator/businesses/gym.py)

## Domain overview

The gym simulator models a fitness studio. Members enroll, check in, book and attend classes, schedule personal training sessions, log workouts, record progress, make retail purchases, fail membership payments, receive retention outreach, freeze memberships, and cancel memberships.

## Systems represented

- Membership CRM and tiered billing
- Access control and check-ins
- Class scheduling and booking
- Personal training scheduling
- Workout and progress tracking
- Retail POS
- Billing retries and dunning
- Retention outreach
- Membership freezes and cancellations

## Operational workflow

1. Seed staff (instructors, trainers, front desk) and an initial member base.
2. Members enroll with a tier and monthly fee.
3. Members check in, classes are scheduled, and members book/cancel/attend classes.
4. Trainers schedule PT sessions and members log workouts and progress.
5. Retail purchases occur at the front desk.
6. Membership payments may fail, triggering retention outreach.
7. Some members freeze or cancel their memberships.

## Event catalog

### `membership_enrolled`

```json
{
  "member_id": "member_0007",
  "membership_id": "mbr_0007",
  "tier": "basic",
  "monthly_fee": 29.0,
  "tax_amount": 2.9,
  "tax_type": "GST",
  "start_date": "2026-08-02",
  "enrolled_at": "2026-08-02T13:21:14.693586+00:00"
}
```

### `check_in_recorded`

```json
{
  "check_in_id": "ci_0001",
  "member_id": "member_0001",
  "location_id": "main_gym",
  "access_method": "app",
  "checked_in_at": "2026-08-02T13:21:16.693586+00:00"
}
```

### `class_scheduled`

```json
{
  "class_id": "cls_0005",
  "class_name": "Zumba class",
  "class_type": "zumba",
  "instructor_id": "staff_0001",
  "capacity": 25,
  "duration_minutes": 30,
  "scheduled_at": "2026-08-03T09:21:15.593586+00:00",
  "created_at": "2026-08-02T13:21:15.593586+00:00"
}
```

### `class_booked`

```json
{
  "booking_id": "bk_0001",
  "member_id": "member_0003",
  "class_id": "cls_0004",
  "class_name": "Zumba class",
  "status": "confirmed",
  "waitlist_position": 0,
  "booked_at": "2026-08-02T13:21:15.493586+00:00"
}
```

### `class_cancelled`

```json
{
  "booking_id": "bk_0004",
  "member_id": "member_0005",
  "class_id": "cls_0002",
  "cancelled_at": "2026-08-02T13:21:19.393586+00:00",
  "late_cancel": false,
  "penalty_applied": false
}
```

### `class_attended`

```json
{
  "attendance_id": "att_0001",
  "booking_id": "bk_0002",
  "member_id": "member_0001",
  "class_id": "cls_0005",
  "checked_in_at": "2026-08-02T13:21:16.393586+00:00"
}
```

### `pt_session_scheduled`

```json
{
  "session_id": "ses_0001",
  "member_id": "member_0001",
  "trainer_id": "staff_0003",
  "session_type": "cardio",
  "duration_minutes": 60,
  "scheduled_at": "2026-08-04T20:21:14.793586+00:00",
  "booked_at": "2026-08-02T13:21:14.793586+00:00"
}
```

### `workout_logged`

```json
{
  "workout_id": "wo_0001",
  "member_id": "member_0008",
  "exercises": [
    {
      "name": "squat",
      "sets": 3,
      "reps": 12,
      "weight_kg": 115.2,
      "duration_minutes": null
    },
    {
      "name": "deadlift",
      "sets": 2,
      "reps": 11,
      "weight_kg": 103.8,
      "duration_minutes": null
    },
    {
      "name": "bench_press",
      "sets": 2,
      "reps": 7,
      "weight_kg": 82.9,
      "duration_minutes": null
    }
  ],
  "logged_at": "2026-08-02T13:21:14.993586+00:00"
}
```

### `progress_recorded`

```json
{
  "log_id": "log_0001",
  "member_id": "member_0005",
  "measurement_type": "bench_press_kg",
  "value": 94.6,
  "recorded_at": "2026-08-02T13:21:17.893586+00:00"
}
```

### `retail_purchase_made`

```json
{
  "purchase_id": "pur_0001",
  "member_id": "member_0004",
  "items": [
    {
      "sku": "WATER_BOTTLE",
      "name": "water_bottle",
      "qty": 3,
      "unit_price": 18.0
    }
  ],
  "subtotal": 54.0,
  "tax_amount": 5.4,
  "tax_type": "GST",
  "total": 59.4,
  "payment_method": "cash",
  "purchased_at": "2026-08-02T13:21:15.893586+00:00"
}
```

### `payment_failed`

```json
{
  "payment_id": "pay_0001",
  "member_id": "member_0006",
  "membership_id": "mbr_0006",
  "amount": 49.0,
  "tax_amount": 4.9,
  "tax_type": "GST",
  "failure_reason": "expired_card",
  "retry_attempt": 1,
  "failed_at": "2026-08-02T13:21:15.993586+00:00"
}
```

### `retention_outreach_sent`

```json
{
  "outreach_id": "out_0001",
  "member_id": "member_0006",
  "channel": "sms",
  "message_type": "payment_retry",
  "sent_at": "2026-08-02T13:21:17.793586+00:00"
}
```

### `membership_renewed`

```json
{
  "member_id": "member_0001",
  "membership_id": "mbr_0001",
  "tier": "standard",
  "monthly_fee": 49.0,
  "tax_amount": 4.9,
  "tax_type": "GST",
  "renewal_term_months": 12,
  "renewed_at": "2026-08-02T13:21:18.093586+00:00"
}
```

### `membership_upgraded`

```json
{
  "member_id": "member_0002",
  "membership_id": "mbr_0002",
  "previous_tier": "basic",
  "new_tier": "premium",
  "new_monthly_fee": 79.0,
  "tax_amount": 7.9,
  "tax_type": "GST",
  "upgraded_at": "2026-08-02T13:21:18.193586+00:00"
}
```

### `membership_downgraded`

```json
{
  "member_id": "member_0003",
  "membership_id": "mbr_0003",
  "previous_tier": "premium",
  "new_tier": "standard",
  "new_monthly_fee": 49.0,
  "tax_amount": 4.9,
  "tax_type": "GST",
  "reason": "cost",
  "downgraded_at": "2026-08-02T13:21:18.293586+00:00"
}
```

### `membership_frozen`

```json
{
  "member_id": "member_0004",
  "membership_id": "mbr_0004",
  "frozen_at": "2026-08-02T13:21:15.793586+00:00",
  "resume_date": "2026-10-24"
}
```

### `membership_cancelled`

```json
{
  "member_id": "member_0004",
  "membership_id": "mbr_0004",
  "cancelled_at": "2026-08-02T13:22:31.439742+00:00",
  "reason": "relocating",
  "churn_risk_score": 0.79
}
```

## Configuration notes

- Tiers: `basic` ($29), `standard` ($49), `premium` ($79). Each tier has a monthly class limit.
- Class types: `yoga`, `spin`, `hiit`, `pilates`, `strength`, `zumba`.
- Initial staff: 4 roles (instructor x2, trainer, front_desk).
- Override initial members with `initial_state_overrides.initial_members` (default `6`).
- Override initial classes with `initial_state_overrides.initial_classes` (default `4`).
- `GymSimulator` implements `restore(snapshot)` to resume the invoice counter during daemon replay.
- A `DaypartScheduler` varies check-ins, classes, and retail by simulated hour.

## CLI quick start

```bash
uv run eden-business-simulator run gym --duration 300 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon gym \
  --stream-id gym_seed42 \
  --storage sqlite --storage-uri gym.db \
  --duration 600 --rate 2 --no-realtime

uv run eden-business-simulator replay gym_seed42 \
  --storage sqlite --storage-uri gym.db --speed 1.0
```
