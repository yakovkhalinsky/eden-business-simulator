"""SaaS subscription simulator (initial stub)."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.models import Clock


class SaaSSimulator(BusinessSimulator):
    """Simulates a SaaS subscription business (minimal first version)."""

    business_type = "saas"

    def __init__(self) -> None:
        self.config = None
        self.faker = Faker()
        self.rng = random.Random()
        self.accounts: list[dict[str, Any]] = []
        self.account_counter = 0
        self.feature_counter = 0

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)
        self.accounts = []
        self.account_counter = 0
        self.feature_counter = 0
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
        event_type = self.rng.choice(self.available_event_types())

        if event_type == "account_signed_up":
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

        if not self.accounts:
            account = self._create_account()
            return {
                "event_type": "account_signed_up",
                "payload": {
                    "account_id": account["account_id"],
                    "email": account["email"],
                    "signup_at": account["signup_at"].isoformat(),
                    "attribution": "organic",
                },
            }

        account = self.rng.choice(self.accounts)
        self.feature_counter += 1
        return {
            "event_type": event_type,
            "payload": {
                "account_id": account["account_id"],
                "event_id": f"evt_{self.feature_counter:06d}",
                "feature_key": self.rng.choice(
                    ["api_call", "report_export", "user_invited", "workflow_run"]
                ),
                "quantity": self.rng.randint(1, 100),
                "recorded_at": clock.now.isoformat(),
            },
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "account_count": len(self.accounts),
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
