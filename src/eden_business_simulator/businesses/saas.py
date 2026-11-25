"""SaaS subscription simulator."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import (
    BusinessSimulator,
    DEFAULT_TAX_TYPE,
    compute_tax,
)
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.models import Clock


class SaaSSimulator(BusinessSimulator):
    """Simulates a SaaS subscription business."""

    business_type = "saas"

    _PLANS = (
        ("starter", 29.0, "month"),
        ("growth", 79.0, "month"),
        ("enterprise", 249.0, "month"),
    )
    _FEATURES = (
        "api_call",
        "report_export",
        "user_invited",
        "workflow_run",
        "storage_gb",
        "support_chat",
    )
    _SEVERITIES = ("low", "medium", "high", "critical")
    _CHURN_REASONS = (
        "too_expensive",
        "missing_features",
        "switched_competitor",
        "project_ended",
        "not_using",
    )

    def __init__(self) -> None:
        self.config = None
        self.faker = Faker()
        self.rng = random.Random()
        self.accounts: list[dict[str, Any]] = []
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.invoices: list[dict[str, Any]] = []
        self.tickets: list[dict[str, Any]] = []
        self.account_counter = 0
        self.feature_counter = 0
        self.invoice_counter = 0
        self.payment_counter = 0
        self.ticket_counter = 0
        self.churn_counter = 0
        self.catalog = WeightedEventCatalog(self.rng)

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)
        self.accounts = []
        self.subscriptions = {}
        self.invoices = []
        self.tickets = []
        self.account_counter = 0
        self.feature_counter = 0
        self.invoice_counter = 0
        self.payment_counter = 0
        self.ticket_counter = 0
        self.churn_counter = 0
        self.catalog = WeightedEventCatalog(self.rng)
        definition = getattr(self, "definition", None)
        if definition is not None:
            domain = definition.domain
            if "plans" in domain:
                self._PLANS = tuple(tuple(row) for row in domain["plans"])
            if "features" in domain:
                self._FEATURES = tuple(domain["features"])
            if "severities" in domain:
                self._SEVERITIES = tuple(domain["severities"])
            if "churn_reasons" in domain:
                self._CHURN_REASONS = tuple(domain["churn_reasons"])
        self._build_catalog()
        initial_accounts = self.config.initial_state_overrides.get("initial_accounts", 3)
        for _ in range(initial_accounts):
            self._create_account()

    def available_event_types(self) -> list[str]:
        return [
            "account_signed_up",
            "plan_subscribed",
            "feature_used",
            "invoice_generated",
            "payment_succeeded",
            "payment_failed",
            "support_ticket_opened",
            "ticket_resolved",
            "churned",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "account_signed_up"
        return self._emit(event_type, clock)

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _build_catalog(self) -> None:
        guards = {"has_accounts": lambda ctx: len(ctx.accounts) > 0}
        if getattr(self, "definition", None) is not None:
            from eden_business_simulator.framework.definition import (
                build_weighted_catalog,
            )

            self.catalog = build_weighted_catalog(self.definition, self.rng, guards)
            return
        self.catalog.register("account_signed_up", base_weight=1.0)
        for event_type in self.available_event_types()[1:]:
            self.catalog.register(
                event_type, base_weight=1.0, guard=guards["has_accounts"]
            )

    def _emit_account_signed_up(self, clock: Clock) -> dict[str, Any]:
        event_type = "account_signed_up"
        account = self._create_account()
        return {
            "event_type": event_type,
            "payload": {
                "account_id": account["account_id"],
                "email": account["email"],
                "signup_at": account["signup_at"].isoformat(),
                "attribution": self.rng.choice(["organic", "paid", "referral"]),
            },
        }

    def _emit_plan_subscribed(self, clock: Clock) -> dict[str, Any]:
        event_type = "plan_subscribed"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        plan_name, monthly_fee, billing_interval = self.rng.choice(self._PLANS)
        subscription_id = f"sub_{len(self.subscriptions) + 1:04d}"
        self.subscriptions[account["account_id"]] = {
            "subscription_id": subscription_id,
            "account_id": account["account_id"],
            "plan_name": plan_name,
            "monthly_fee": monthly_fee,
            "billing_interval": billing_interval,
            "status": "active",
        }
        return {
            "event_type": "plan_subscribed",
            "payload": {
                "account_id": account["account_id"],
                "subscription_id": subscription_id,
                "plan_name": plan_name,
                "monthly_fee": monthly_fee,
                "tax_amount": compute_tax(monthly_fee),
                "tax_type": DEFAULT_TAX_TYPE,
                "billing_interval": billing_interval,
                "subscribed_at": clock.now.isoformat(),
            },
        }

    def _emit_feature_used(self, clock: Clock) -> dict[str, Any]:
        event_type = "feature_used"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        self.feature_counter += 1
        feature_key = self.rng.choice(self._FEATURES)
        quantity = self.rng.randint(1, 100)
        return {
            "event_type": "feature_used",
            "payload": {
                "account_id": account["account_id"],
                "event_id": f"evt_{self.feature_counter:06d}",
                "feature_key": feature_key,
                "quantity": quantity,
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_invoice_generated(self, clock: Clock) -> dict[str, Any]:
        event_type = "invoice_generated"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        self.invoice_counter += 1
        invoice_id = f"inv_{self.invoice_counter:06d}"
        subscription = self.subscriptions.get(account["account_id"])
        monthly_fee = subscription["monthly_fee"] if subscription else 49.0
        tax_amount = compute_tax(monthly_fee)
        total = round(monthly_fee + tax_amount, 2)
        invoice = {
            "invoice_id": invoice_id,
            "account_id": account["account_id"],
            "amount": total,
            "tax_amount": tax_amount,
            "tax_type": DEFAULT_TAX_TYPE,
            "status": "open",
        }
        self.invoices.append(invoice)
        return {
            "event_type": "invoice_generated",
            "payload": {
                "invoice_id": invoice_id,
                "account_id": account["account_id"],
                "line_items": [
                    {
                        "description": "subscription",
                        "amount": monthly_fee,
                    },
                    {
                        "description": "tax",
                        "amount": tax_amount,
                    },
                ],
                "total": total,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "due_date": (clock.now + self.rng.choice([
                    timedelta(days=7),
                    timedelta(days=14),
                    timedelta(days=30),
                ])).date().isoformat(),
                "generated_at": clock.now.isoformat(),
            },
        }

    def _emit_payment_succeeded(self, clock: Clock) -> dict[str, Any]:
        event_type = "payment_succeeded"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        account_invoices = [inv for inv in self.invoices if inv["account_id"] == account["account_id"] and inv["status"] == "open"]
        if not account_invoices:
            # Fall back to generating an invoice, then record payment against it.
            return self._emit_invoice_generated_for_account(account, clock)
        self.payment_counter += 1
        invoice = self.rng.choice(account_invoices)
        invoice["status"] = "paid"
        return {
            "event_type": "payment_succeeded",
            "payload": {
                "payment_id": f"pay_{self.payment_counter:06d}",
                "account_id": account["account_id"],
                "invoice_id": invoice["invoice_id"],
                "amount": invoice["amount"],
                "tax_amount": invoice["tax_amount"],
                "tax_type": invoice["tax_type"],
                "currency": "USD",
                "payment_method": self.rng.choice(["card", "ach", "wire"]),
                "succeeded_at": clock.now.isoformat(),
            },
        }

    def _emit_payment_failed(self, clock: Clock) -> dict[str, Any]:
        event_type = "payment_failed"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        subscription = self.subscriptions.get(account["account_id"])
        amount = subscription["monthly_fee"] if subscription else 49.0
        return {
            "event_type": "payment_failed",
            "payload": {
                "account_id": account["account_id"],
                "subscription_id": subscription["subscription_id"] if subscription else None,
                "amount": amount,
                "tax_amount": compute_tax(amount),
                "tax_type": DEFAULT_TAX_TYPE,
                "currency": "USD",
                "failure_reason": self.rng.choice(["insufficient_funds", "expired_card", "bank_declined"]),
                "attempt_number": self.rng.randint(1, 3),
                "failed_at": clock.now.isoformat(),
            },
        }

    def _emit_support_ticket_opened(self, clock: Clock) -> dict[str, Any]:
        event_type = "support_ticket_opened"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        self.ticket_counter += 1
        ticket_id = f"tkt_{self.ticket_counter:04d}"
        ticket = {
            "ticket_id": ticket_id,
            "account_id": account["account_id"],
            "status": "open",
        }
        self.tickets.append(ticket)
        return {
            "event_type": "support_ticket_opened",
            "payload": {
                "ticket_id": ticket_id,
                "account_id": account["account_id"],
                "subject": self.faker.sentence(nb_words=6),
                "severity": self.rng.choice(self._SEVERITIES),
                "channel": self.rng.choice(["email", "chat", "phone"]),
                "opened_at": clock.now.isoformat(),
            },
        }

    def _emit_ticket_resolved(self, clock: Clock) -> dict[str, Any]:
        event_type = "ticket_resolved"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        open_tickets = [t for t in self.tickets if t["account_id"] == account["account_id"] and t["status"] == "open"]
        if not open_tickets:
            return self._emit_support_ticket_opened_for_account(account, clock)
        ticket = self.rng.choice(open_tickets)
        ticket["status"] = "resolved"
        return {
            "event_type": "ticket_resolved",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "account_id": account["account_id"],
                "resolution": self.rng.choice(["fixed", "answered", "escalated", "refunded"]),
                "resolved_at": clock.now.isoformat(),
            },
        }

    def _emit_churned(self, clock: Clock) -> dict[str, Any]:
        event_type = "churned"
        if not self.accounts:
            return self._emit_account_signed_up(clock)
        account = self.rng.choice(self.accounts)
        subscription = self.subscriptions.get(account["account_id"])
        if subscription:
            subscription["status"] = "cancelled"
        self.churn_counter += 1
        return {
            "event_type": "churned",
            "payload": {
                "account_id": account["account_id"],
                "subscription_id": subscription["subscription_id"] if subscription else None,
                "reason": self.rng.choice(self._CHURN_REASONS),
                "churned_at": clock.now.isoformat(),
            },
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "account_count": len(self.accounts),
            "subscription_count": len(self.subscriptions),
            "invoice_count": len(self.invoices),
            "ticket_count": len(self.tickets),
        }

    def position_snapshot(self) -> dict[str, Any]:
        active_subs = [
            s for s in self.subscriptions.values() if s["status"] == "active"
        ]
        open_invoices = [i for i in self.invoices if i["status"] == "open"]
        paid_invoices = [i for i in self.invoices if i["status"] == "paid"]
        return {
            "schema_version": 1,
            "business_type": self.business_type,
            "position": {
                "active_subscriptions": len(active_subs),
                "mrr": round(sum(s["monthly_fee"] for s in active_subs), 2),
                "account_count": len(self.accounts),
                "open_invoices_count": len(open_invoices),
                "open_invoices_amount": round(
                    sum(i["amount"] for i in open_invoices), 2
                ),
                "paid_revenue_total": round(
                    sum(i["amount"] for i in paid_invoices), 2
                ),
                "open_tickets_count": sum(
                    1 for t in self.tickets if t["status"] == "open"
                ),
                "churned_count": self.churn_counter,
            },
        }

    def _create_account(self) -> dict[str, Any]:
        self.account_counter += 1
        account = {
            "account_id": f"acc_{self.account_counter:04d}",
            "email": self.faker.email(),
            "signup_at": datetime.now(timezone.utc),
        }
        self.accounts.append(account)
        return account

    def _emit_invoice_generated_for_account(self, account: dict[str, Any], clock: Clock) -> dict[str, Any]:
        self.invoice_counter += 1
        invoice_id = f"inv_{self.invoice_counter:06d}"
        subscription = self.subscriptions.get(account["account_id"])
        monthly_fee = subscription["monthly_fee"] if subscription else 49.0
        tax_amount = compute_tax(monthly_fee)
        total = round(monthly_fee + tax_amount, 2)
        invoice = {
            "invoice_id": invoice_id,
            "account_id": account["account_id"],
            "amount": total,
            "tax_amount": tax_amount,
            "tax_type": DEFAULT_TAX_TYPE,
            "status": "open",
        }
        self.invoices.append(invoice)
        return {
            "event_type": "invoice_generated",
            "payload": {
                "invoice_id": invoice_id,
                "account_id": account["account_id"],
                "line_items": [
                    {"description": "subscription", "amount": monthly_fee},
                    {"description": "tax", "amount": tax_amount},
                ],
                "total": total,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "due_date": (clock.now + self.rng.choice([
                    timedelta(days=7),
                    timedelta(days=14),
                    timedelta(days=30),
                ])).date().isoformat(),
                "generated_at": clock.now.isoformat(),
            },
        }

    def _emit_support_ticket_opened_for_account(self, account: dict[str, Any], clock: Clock) -> dict[str, Any]:
        self.ticket_counter += 1
        ticket_id = f"tkt_{self.ticket_counter:04d}"
        ticket = {
            "ticket_id": ticket_id,
            "account_id": account["account_id"],
            "status": "open",
        }
        self.tickets.append(ticket)
        return {
            "event_type": "support_ticket_opened",
            "payload": {
                "ticket_id": ticket_id,
                "account_id": account["account_id"],
                "subject": self.faker.sentence(nb_words=6),
                "severity": self.rng.choice(self._SEVERITIES),
                "channel": self.rng.choice(["email", "chat", "phone"]),
                "opened_at": clock.now.isoformat(),
            },
        }

