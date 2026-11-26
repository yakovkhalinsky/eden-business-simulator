"""E-commerce retail simulator."""

from __future__ import annotations

import copy
import random
from datetime import datetime, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import (
    ActionRejected,
    BusinessSimulator,
    DEFAULT_TAX_TYPE,
    compute_tax,
)
from eden_business_simulator.framework.catalog import WeightedEventCatalog
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
        self.catalog = WeightedEventCatalog(self.rng)

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
        self.catalog = WeightedEventCatalog(self.rng)
        definition = getattr(self, "definition", None)
        if definition is not None and "currencies" in definition.domain:
            self._CURRENCIES = tuple(definition.domain["currencies"])
        self._build_catalog()

        initial_products = self.config.initial_state_overrides.get(
            "initial_products", 20
        )
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
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "customer_created"
        self.event_counter += 1
        return self._emit(event_type, clock)

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _build_catalog(self) -> None:
        guards = {
            "has_customers_and_products": lambda ctx: len(ctx.customers) > 0
            and len(ctx.products) > 0,
            "has_cart_items": lambda ctx: any(
                items for items in ctx.carts.values()
            ),
            "has_placed_orders": lambda ctx: any(
                o["status"] == "placed" for o in ctx.orders
            ),
            "has_paid_orders": lambda ctx: any(
                o["status"] == "paid" for o in ctx.orders
            ),
            "has_products": lambda ctx: len(ctx.products) > 0,
            "has_shipped_orders": lambda ctx: any(
                o["status"] == "shipped" for o in ctx.orders
            ),
        }
        if getattr(self, "definition", None) is not None:
            from eden_business_simulator.framework.definition import (
                build_weighted_catalog,
            )

            self.catalog = build_weighted_catalog(self.definition, self.rng, guards)
            return
        for event_type, weight, guard_name in [
            ("customer_created", 5.0, None),
            ("product_viewed", 30.0, "has_customers_and_products"),
            ("cart_updated", 20.0, "has_customers_and_products"),
            ("checkout_started", 8.0, "has_cart_items"),
            ("payment_info_entered", 6.0, "has_cart_items"),
            ("order_placed", 10.0, "has_cart_items"),
            ("payment_processed", 15.0, "has_placed_orders"),
            ("order_shipped", 5.0, "has_paid_orders"),
            ("inventory_adjusted", 5.0, "has_products"),
            ("refund_issued", 2.0, "has_shipped_orders"),
        ]:
            self.catalog.register(
                event_type,
                base_weight=weight,
                guard=guards[guard_name] if guard_name else None,
            )

    def _emit_customer_created(self, clock: Clock) -> dict[str, Any]:
        event_type = "customer_created"
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

    def _emit_product_viewed(self, clock: Clock) -> dict[str, Any]:
        event_type = "product_viewed"
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

    def _emit_cart_updated(self, clock: Clock) -> dict[str, Any]:
        event_type = "cart_updated"
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

    def _emit_checkout_started(self, clock: Clock) -> dict[str, Any]:
        event_type = "checkout_started"
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

    def _emit_payment_info_entered(self, clock: Clock) -> dict[str, Any]:
        event_type = "payment_info_entered"
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

    def _emit_order_placed(self, clock: Clock) -> dict[str, Any]:
        event_type = "order_placed"
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

    def _emit_payment_processed(self, clock: Clock) -> dict[str, Any]:
        event_type = "payment_processed"
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

    def _emit_order_shipped(self, clock: Clock) -> dict[str, Any]:
        event_type = "order_shipped"
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

    def _emit_inventory_adjusted(self, clock: Clock) -> dict[str, Any]:
        event_type = "inventory_adjusted"
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

    def _emit_refund_issued(self, clock: Clock) -> dict[str, Any]:
        event_type = "refund_issued"
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

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "customer_count": len(self.customers),
            "product_count": len(self.products),
            "order_count": len(self.orders),
            "orders_by_status": self._orders_by_status(),
        }

    # ------------------------------------------------------------------
    # Full-state snapshot / restore (restorable state, S3)
    # ------------------------------------------------------------------

    def serialize_state(self) -> dict[str, Any]:
        """Capture every piece of mutable business state.

        Ecommerce has no IdGenerator (the framework section therefore
        carries no id counters for it): its three ad-hoc counters are
        captured explicitly here. Product and customer IDs derive from
        ``len(...)`` of their collections, which stays correct only
        because the collections themselves are restored faithfully.
        Customers carry real ``datetime`` values in ``registered_at``
        and orders in ``placed_at``; the framework codec encodes them
        explicitly. Product dicts are captured whole, so action-mutated
        prices and inventory survive, as do orders' action-added
        ``expedited``/``carrier`` fields. The definition-shadowed
        currency tuple is captured on the trades precedent.
        """
        return {
            "customers": copy.deepcopy(self.customers),
            "products": copy.deepcopy(self.products),
            "carts": copy.deepcopy(self.carts),
            "orders": copy.deepcopy(self.orders),
            "order_counter": self.order_counter,
            "payment_counter": self.payment_counter,
            "event_counter": self.event_counter,
            "currencies": list(self._CURRENCIES),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Replace all business state from :meth:`serialize_state`.

        Runs after a fresh ``initialize()`` on the daemon path, so
        every attribute captured above is overwritten wholesale —
        nothing from the initial state may leak through.
        """
        self.customers = copy.deepcopy(state["customers"])
        self.products = copy.deepcopy(state["products"])
        self.carts = copy.deepcopy(state["carts"])
        self.orders = copy.deepcopy(state["orders"])
        self.order_counter = state["order_counter"]
        self.payment_counter = state["payment_counter"]
        self.event_counter = state["event_counter"]
        if "currencies" in state:
            self._CURRENCIES = tuple(state["currencies"])

    def position_snapshot(self) -> dict[str, Any]:
        paid_orders = [
            o
            for o in self.orders
            if o["status"] in ("paid", "shipped", "refunded")
        ]
        refunded = [o for o in self.orders if o["status"] == "refunded"]
        active_carts = {cid: items for cid, items in self.carts.items() if items}
        return {
            "schema_version": 1,
            "business_type": self.business_type,
            "position": {
                "revenue_total": round(sum(o["total"] for o in paid_orders), 2),
                "orders_by_status": self._orders_by_status(),
                "inventory": {
                    p["product_id"]: p["inventory"] for p in self.products
                },
                "active_carts_count": len(active_carts),
                "active_cart_items": sum(
                    item["quantity"]
                    for items in active_carts.values()
                    for item in items
                ),
                "refunds_total": round(sum(o["total"] for o in refunded), 2),
            },
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

    def _orders_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for order in self.orders:
            counts[order["status"]] = counts.get(order["status"], 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Action handlers (Phase 4c)
    #
    # New action-only effect type: product_price_adjusted,
    # order_expedited. Handlers validate before mutating and consume
    # no RNG.
    # ------------------------------------------------------------------

    def _product_or_reject(self, product_id: str) -> dict[str, Any]:
        product = next(
            (p for p in self.products if p["product_id"] == product_id),
            None,
        )
        if product is None:
            raise ActionRejected(f"unknown_product:{product_id}")
        return product

    def _order_or_reject(self, order_id: str) -> dict[str, Any]:
        order = next(
            (o for o in self.orders if o["order_id"] == order_id), None
        )
        if order is None:
            raise ActionRejected(f"unknown_order:{order_id}")
        return order

    def _apply_adjust_price(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        product = self._product_or_reject(params["product_id"])
        old_price = product["price"]
        new_price = round(float(params["new_price"]), 2)
        product["price"] = new_price
        return {
            "event_type": "product_price_adjusted",
            "payload": {
                "product_id": product["product_id"],
                "sku": product["sku"],
                "old_price": old_price,
                "new_price": new_price,
                "adjusted_at": clock.now.isoformat(),
            },
        }

    def _apply_restock_product(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        product = self._product_or_reject(params["product_id"])
        qty = params["qty"]
        product["inventory"] += qty
        return {
            "event_type": "inventory_adjusted",
            "payload": {
                "product_id": product["product_id"],
                "sku": product["sku"],
                "delta": qty,
                "new_inventory": product["inventory"],
                "reason": "restock",
                "adjusted_at": clock.now.isoformat(),
            },
        }

    def _apply_issue_refund(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        order = self._order_or_reject(params["order_id"])
        if order["status"] not in ("paid", "shipped"):
            raise ActionRejected(f"order_not_refundable:{params['order_id']}")
        order["status"] = "refunded"
        return {
            "event_type": "refund_issued",
            "payload": {
                "order_id": order["order_id"],
                "amount": order["total"],
                "tax_amount": order["tax_amount"],
                "tax_type": order["tax_type"],
                "currency": order["currency"],
                "reason": params.get("reason", "manager_goodwill"),
                "issued_at": clock.now.isoformat(),
            },
        }

    def _apply_expedite_order(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        order = self._order_or_reject(params["order_id"])
        if order["status"] != "paid":
            raise ActionRejected(f"order_not_paid:{params['order_id']}")
        carrier = params.get("carrier")
        order["expedited"] = True
        if carrier is not None:
            order["carrier"] = carrier
        return {
            "event_type": "order_expedited",
            "payload": {
                "order_id": order["order_id"],
                "carrier": carrier,
                "expedited_at": clock.now.isoformat(),
            },
        }
