# SaaS simulator

Slug: `saas`  
Source module: [`src/eden_business_simulator/businesses/saas.py`](../../src/eden_business_simulator/businesses/saas.py)

## Domain overview

The SaaS simulator models a subscription software business. Accounts sign up, subscribe to plans, use features, receive invoices, make or fail payments, open support tickets, have tickets resolved, and eventually churn.

## Systems represented

- Account signup and attribution
- Subscription plans and billing
- Feature usage metering
- Invoicing and tax
- Payment success and failure
- Support tickets
- Churn

## Operational workflow

1. Seed an initial account base.
2. Accounts sign up with an attribution channel.
3. Accounts subscribe to plans and consume features.
4. Invoices are generated and payments succeed or fail.
5. Support tickets are opened and resolved.
6. Some accounts churn.

## Event catalog

### `account_signed_up`

```json
{
  "account_id": "acc_0004",
  "email": "barrypeter@example.net",
  "signup_at": "2026-08-02T13:21:12.768564+00:00",
  "attribution": "paid"
}
```

### `plan_subscribed`

```json
{
  "account_id": "acc_0002",
  "subscription_id": "sub_0001",
  "plan_name": "growth",
  "monthly_fee": 79.0,
  "tax_amount": 7.9,
  "tax_type": "GST",
  "billing_interval": "month",
  "subscribed_at": "2026-08-02T13:21:12.868564+00:00"
}
```

### `feature_used`

```json
{
  "account_id": "acc_0003",
  "event_id": "evt_000001",
  "feature_key": "api_call",
  "quantity": 33,
  "recorded_at": "2026-08-02T13:21:12.767472+00:00"
}
```

### `invoice_generated`

```json
{
  "invoice_id": "inv_000001",
  "account_id": "acc_0001",
  "line_items": [
    {
      "description": "subscription",
      "amount": 79.0
    },
    {
      "description": "tax",
      "amount": 7.9
    }
  ],
  "total": 86.9,
  "tax_amount": 7.9,
  "tax_type": "GST",
  "due_date": "2026-08-09",
  "generated_at": "2026-08-02T13:21:13.267472+00:00"
}
```

### `payment_succeeded`

```json
{
  "payment_id": "pay_000001",
  "account_id": "acc_0001",
  "invoice_id": "inv_000001",
  "amount": 86.9,
  "tax_amount": 7.9,
  "tax_type": "GST",
  "currency": "USD",
  "payment_method": "card",
  "succeeded_at": "2026-08-02T13:21:14.067472+00:00"
}
```

### `payment_failed`

```json
{
  "account_id": "acc_0005",
  "subscription_id": "sub_0002",
  "amount": 29.0,
  "tax_amount": 2.9,
  "tax_type": "GST",
  "currency": "USD",
  "failure_reason": "expired_card",
  "attempt_number": 1,
  "failed_at": "2026-08-02T13:21:15.667472+00:00"
}
```

### `support_ticket_opened`

```json
{
  "ticket_id": "tkt_0001",
  "account_id": "acc_0001",
  "subject": "Unable to export the quarterly report.",
  "severity": "high",
  "channel": "email",
  "opened_at": "2026-08-02T13:21:12.967472+00:00"
}
```

### `ticket_resolved`

```json
{
  "ticket_id": "tkt_0001",
  "account_id": "acc_0001",
  "resolution": "fixed",
  "resolved_at": "2026-08-02T13:21:14.667472+00:00"
}
```

### `churned`

```json
{
  "account_id": "acc_0001",
  "subscription_id": "sub_0001",
  "reason": "too_expensive",
  "churned_at": "2026-08-02T13:21:13.467472+00:00"
}
```

## Configuration notes

- Override the initial account count with `initial_state_overrides.initial_accounts` (default `3`).
- Plans: `starter` ($29), `growth` ($79), `enterprise` ($249), all billed monthly.
- Feature keys: `api_call`, `report_export`, `user_invited`, `workflow_run`, `storage_gb`, `support_chat`.
- Attribution values: `organic`, `paid`, `referral`.
- `payment_succeeded` references an existing open invoice when one is available.

## CLI quick start

```bash
uv run eden-business-simulator run saas --duration 60 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon saas \
  --stream-id saas_seed42 \
  --storage sqlite --storage-uri saas.db \
  --duration 120 --rate 2 --no-realtime
```
