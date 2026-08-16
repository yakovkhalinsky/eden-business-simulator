"""E-commerce retail simulator."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import (
    BusinessSimulator,
    DEFAULT_TAX_TYPE,
    compute_tax,
)
from eden_business_simulator.models import Clock


class EcommerceSimulator(BusinessSimulator):
    """Simulates an online retail store."""

    business_type = "ecommerce"

    _CURRENCIES = ("USD", "EUR", "GBP", "AUD", "CAD")

    def __init__(self) -> None:
        self.config = None
        self.faker = Faker()
        self.rng = random.Random()

        self.customers: list[dict[str, Any]] = []
        self.products: list[dict[str, Any]] = []
        self.carts: dict[str, list[dict[str, Any]]] = {}
        self.orders: list[dict[str, Any]] = []
        self.order_counter = 0
        self.payment_counter = 0
        self.event_counter = 0

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)

        self.customers = []
        self.products = []
        self.carts = {}
        self.orders = []
        self.order_counter = 0
        self.payment_counter = 0
        self.event_counter = 0

        initial_products = 20
        initial_customers = self.config.initial_state_overrides.get(
            "initial_customers", 5
        )

        for _ in range(initial_products):
            self.products.append(self._create_product())

        for _ in range(initial_customers):
            self._create_customer()

    def available_event_types(self) -> list[str]:
        return [
            "customer_created",
            "product_viewed",
            "cart_updated",
            "checkout_started",
            "payment_info_entered",
            "order_placed",
            "payment_processed",
            "order_shipped",
            "inventory_adjusted",
            "refund_issued",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self._choose_event_type()
        self.event_counter += 1

        if event_type == "customer_created":
            customer = self._create_customer()
            return {
                "event_type": event_type,
                "payload": {
                    "customer_id": customer["customer_id"],
                    "name": customer["name"],
                    "email": customer["email"],
                    "registered_at": customer["registered_at"].isoformat(),
                },
            }

        if event_type == "product_viewed":
            customer = self.rng.choice(self.customers)
            product = self.rng.choice(self.products)
            return {
                "event_type": event_type,
                "payload": {
                    "customer_id": customer["customer_id"],
                    "product_id": product["product_id"],
                    "viewed_at": clock.now.isoformat(),
                },
            }

        if event_type == "cart_updated":
            customer = self.rng.choice(self.customers)
            product = self.rng.choice(self.products)
            qty = self.rng.randint(1, 3)
            if self.rng.random() < 0.2:
                qty = 0
            self._update_cart(customer["customer_id"], product, qty)
            return {
                "event_type": event_type,
                "payload": {
                    "customer_id": customer["customer_id"],
                    "items": [
                        {"product_id": item["product_id"], "quantity": item["quantity"]}
                        for item in self.carts.get(customer["customer_id"], [])
                    ],
                    "updated_at": clock.now.isoformat(),
                },
            }

        if event_type == "checkout_started":
            customers_with_items = [cid for cid, items in self.carts.items() if items]
            if not customers_with_items:
                customer = self.rng.choice(self.customers)
                product = self.rng.choice(self.products)
                self._update_cart(customer["customer_id"], product, 1)
                customers_with_items = [customer["customer_id"]]
            customer_id = self.rng.choice(customers_with_items)
            return {
                "event_type": event_type,
                "payload": {
                    "customer_id": customer_id,
                    "cart_items": [
                        {"product_id": item["product_id"], "quantity": item["quantity"]}
                        for item in self.carts.get(customer_id, [])
                    ],
                    "started_at": clock.now.isoformat(),
                },
            }

        if event_type == "payment_info_entered":
            customers_with_items = [cid for cid, items in self.carts.items() if items]
            if not customers_with_items:
                customer = self.rng.choice(self.customers)
                product = self.rng.choice(self.products)
                self._update_cart(customer["customer_id"], product, 1)
                customers_with_items = [customer["customer_id"]]
            customer_id = self.rng.choice(customers_with_items)
            return {
                "event_type": event_type,
                "payload": {
                    "customer_id": customer_id,
                    "payment_method": self.rng.choice(["card", "paypal", "apple_pay"]),
                    "entered_at": clock.now.isoformat(),
                },
            }

        if event_type == "order_placed":
            customer, order = self._place_order(clock)
            return {
                "event_type": event_type,
                "payload": {
                    "order_id": order["order_id"],
                    "customer_id": customer["customer_id"],
                    "items": order["items"],
                    "placed_at": order["placed_at"].isoformat(),
                    "subtotal": order["subtotal"],
                    "tax_amount": order["tax_amount"],
                    "tax_type": order["tax_type"],
                    "total": order["total"],
                    "currency": order["currency"],
                },
            }

        if event_type == "payment_processed":
            order = self.rng.choice(
                [o for o in self.orders if o["status"] == "placed"]
            )
            self.payment_counter += 1
            order["status"] = "paid"
            order["payment_id"] = f"pay_{self.payment_counter:06d}"
            status = self.rng.choice(["approved", "approved", "approved", "declined"])
            if status == "declined":
                order["status"] = "payment_failed"
            return {
                "event_type": event_type,
                "payload": {
                    "order_id": order["order_id"],
                    "payment_id": order["payment_id"],
                    "amount": order["total"],
                    "tax_amount": order["tax_amount"],
                    "tax_type": order["tax_type"],
                    "currency": order["currency"],
                    "status": status,
                    "processed_at": clock.now.isoformat(),
                },
            }

        if event_type == "order_shipped":
            order = self.rng.choice(
                [o for o in self.orders if o["status"] == "paid"]
            )
            order["status"] = "shipped"
            return {
                "event_type": event_type,
                "payload": {
                    "order_id": order["order_id"],
                    "shipped_at": clock.now.isoformat(),
                    "carrier": self.rng.choice(["FedEx", "UPS", "DHL", "USPS"]),
                    "tracking_number": self.faker.bothify("TRK-##########"),
                },
            }

        if event_type == "inventory_adjusted":
            product = self.rng.choice(self.products)
            delta = self.rng.choice([-5, -3, -1, 10, 20, 50])
            reason = self.rng.choice(["sale", "restock", "correction", "return"])
            product["inventory"] += delta
            return {
                "event_type": event_type,
                "payload": {
                    "product_id": product["product_id"],
                    "sku": product["sku"],
                    "delta": delta,
                    "new_inventory": product["inventory"],
                    "reason": reason,
                    "adjusted_at": clock.now.isoformat(),
                },
            }

        if event_type == "refund_issued":
            order = self.rng.choice(
                [o for o in self.orders if o["status"] == "shipped"]
            )
            order["status"] = "refunded"
            return {
                "event_type": event_type,
                "payload": {
                    "order_id": order["order_id"],
                    "amount": order["total"],
                    "tax_amount": order["tax_amount"],
                    "tax_type": order["tax_type"],
                    "currency": order["currency"],
                    "reason": self.rng.choice(["defective", "not_as_described", "changed_mind"]),
                    "issued_at": clock.now.isoformat(),
                },
            }

        raise RuntimeError(f"Unhandled event type: {event_type}")

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "customer_count": len(self.customers),
            "product_count": len(self.products),
            "order_count": len(self.orders),
            "orders_by_status": self._orders_by_status(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_product(self) -> dict[str, Any]:
        sku = self.faker.bothify("SKU-####").upper()
        price = round(self.rng.uniform(5.0, 250.0), 2)
        return {
            "product_id": f"prod_{len(self.products):04d}",
            "sku": sku,
            "name": self.faker.catch_phrase(),
            "price": price,
            "inventory": self.rng.randint(20, 500),
        }

    def _create_customer(self) -> dict[str, Any]:
        customer = {
            "customer_id": f"cust_{len(self.customers):04d}",
            "name": self.faker.name(),
            "email": self.faker.email(),
            "registered_at": datetime.now(timezone.utc),
        }
        self.customers.append(customer)
        self.carts[customer["customer_id"]] = []
        return customer

    def _update_cart(
        self,
        customer_id: str,
        product: dict[str, Any],
        quantity: int,
    ) -> None:
        cart = self.carts.setdefault(customer_id, [])
        existing = next(
            (item for item in cart if item["product_id"] == product["product_id"]),
            None,
        )
        if existing is not None:
            if quantity <= 0:
                cart.remove(existing)
            else:
                existing["quantity"] = quantity
        elif quantity > 0:
            cart.append({"product_id": product["product_id"], "quantity": quantity})

    def _place_order(self, clock: Clock) -> tuple[dict[str, Any], dict[str, Any]]:
        customers_with_items = [
            cid for cid, items in self.carts.items() if items
        ]
        if not customers_with_items:
            customer = self.rng.choice(self.customers)
            product = self.rng.choice(self.products)
            self._update_cart(customer["customer_id"], product, 1)
            customers_with_items = [customer["customer_id"]]

        customer_id = self.rng.choice(customers_with_items)
        customer = next(c for c in self.customers if c["customer_id"] == customer_id)
        cart_items = self.carts[customer_id]
        items = []
        total = 0.0
        for cart_item in cart_items:
            product = next(
                p for p in self.products if p["product_id"] == cart_item["product_id"]
            )
            line_total = round(cart_item["quantity"] * product["price"], 2)
            items.append(
                {
                    "product_id": product["product_id"],
                    "sku": product["sku"],
                    "quantity": cart_item["quantity"],
                    "unit_price": product["price"],
                    "line_total": line_total,
                }
            )
            total += line_total

        self.order_counter += 1
        currency = self.rng.choice(self._CURRENCIES)
        subtotal = round(total, 2)
        tax_amount = compute_tax(subtotal)
        order_total = round(subtotal + tax_amount, 2)
        order = {
            "order_id": f"ord_{self.order_counter:06d}",
            "customer_id": customer_id,
            "items": items,
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "tax_type": DEFAULT_TAX_TYPE,
            "total": order_total,
            "currency": currency,
            "status": "placed",
            "placed_at": clock.now,
        }
        self.orders.append(order)
        self.carts[customer_id] = []
        return customer, order

    def _choose_event_type(self) -> str:
        if not self.customers:
            return "customer_created"

        weights: dict[str, float] = {
            "customer_created": 5.0,
            "product_viewed": 30.0,
            "cart_updated": 20.0,
            "inventory_adjusted": 5.0,
        }

        if any(self.carts[cid] for cid in self.carts):
            weights["checkout_started"] = 8.0
            weights["payment_info_entered"] = 6.0
            weights["order_placed"] = 10.0

        if any(o["status"] == "placed" for o in self.orders):
            weights["payment_processed"] = 15.0

        if any(o["status"] == "paid" for o in self.orders):
            weights["order_shipped"] = 5.0

        if any(o["status"] == "shipped" for o in self.orders):
            weights["refund_issued"] = 2.0

        types = list(weights.keys())
        values = list(weights.values())
        return self.rng.choices(types, weights=values, k=1)[0]

    def _orders_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for order in self.orders:
            counts[order["status"]] = counts.get(order["status"], 0) + 1
        return counts
