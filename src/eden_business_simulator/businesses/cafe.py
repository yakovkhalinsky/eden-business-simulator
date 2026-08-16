"""Cafe / hospitality simulator."""

from __future__ import annotations

import random
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import (
    BusinessSimulator,
    DEFAULT_TAX_TYPE,
    compute_tax,
)
from eden_business_simulator.framework.actors import (
    ActorPool,
    MenuCatalog,
    RecipeComponent,
    StaffRoster,
)
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.framework.inventory import InventoryLedger, RecipeBook
from eden_business_simulator.framework.scheduler import DaypartScheduler
from eden_business_simulator.framework.state_machine import TransitionModel
from eden_business_simulator.models import Clock


class CafeSimulator(BusinessSimulator):
    """Simulates a small cafe over a single trading day."""

    business_type = "cafe"

    _MENU = [
        (
            "oat flat white",
            "coffee",
            5.50,
            [
                RecipeComponent("ing_001", "espresso", 0.036, "kg"),
                RecipeComponent("ing_002", "oat_milk", 0.24, "l"),
            ],
        ),
        (
            "espresso",
            "coffee",
            3.00,
            [RecipeComponent("ing_001", "espresso", 0.018, "kg")],
        ),
        (
            "croissant",
            "pastry",
            4.50,
            [
                RecipeComponent("ing_003", "pastry_dough", 0.08, "kg"),
                RecipeComponent("ing_004", "butter", 0.02, "kg"),
            ],
        ),
        (
            "eggs benedict",
            "brunch",
            16.00,
            [
                RecipeComponent("ing_005", "eggs", 2.0, "each"),
                RecipeComponent("ing_006", "ham", 0.06, "kg"),
                RecipeComponent("ing_007", "hollandaise", 0.08, "l"),
            ],
        ),
        (
            "fresh orange juice",
            "cold",
            6.00,
            [
                RecipeComponent("ing_008", "oranges", 3.0, "each"),
                RecipeComponent("ing_009", "ice", 0.1, "kg"),
            ],
        ),
    ]

    _INGREDIENTS = [
        ("ing_001", "espresso", "kg", 18.0),
        ("ing_002", "oat_milk", "l", 1.8),
        ("ing_003", "pastry_dough", "kg", 6.0),
        ("ing_004", "butter", "kg", 8.0),
        ("ing_005", "eggs", "each", 0.5),
        ("ing_006", "ham", "kg", 12.0),
        ("ing_007", "hollandaise", "l", 15.0),
        ("ing_008", "oranges", "each", 0.8),
        ("ing_009", "ice", "kg", 1.0),
    ]

    _TABLES = [
        ("tbl_01", 2),
        ("tbl_02", 2),
        ("tbl_03", 4),
        ("tbl_04", 4),
        ("tbl_05", 6),
        ("tbl_06", 2),
        ("tbl_07", 2),
        ("tbl_08", 4),
    ]

    _STAFF = [
        ("manager", "Avery Manager"),
        ("barista", "Jordan Barista"),
        ("barista", "Taylor Barista"),
        ("server", "Morgan Server"),
        ("chef", "Casey Chef"),
    ]

    _SUPPLIER = "sup_01"

    def __init__(self) -> None:
        self.config = None
        self.rng = random.Random()
        self.faker = Faker()

        self.id_gen = IdGenerator(self.rng)
        self.staff = StaffRoster(self.id_gen, self.rng, self.faker)
        self.menu = MenuCatalog(self.id_gen, self.rng, self.faker)
        self.members = ActorPool(
            self.id_gen,
            "member",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.tables: dict[str, dict[str, Any]] = {}

        self.recipe_book = RecipeBook()
        self.ledger: InventoryLedger | None = None

        self.order_machine = TransitionModel(
            {
                "new": ("fired",),
                "fired": ("preparing", "ready"),
                "preparing": ("ready",),
                "ready": ("paid",),
            }
        )
        self.ticket_machine = TransitionModel(
            {
                "new": ("fired",),
                "fired": ("ready",),
            }
        )

        self.catalog = WeightedEventCatalog(self.rng)
        self.scheduler = DaypartScheduler.default_cafe_schedule()

        self.orders: list[dict[str, Any]] = []
        self.tickets: list[dict[str, Any]] = []

        self._shift_opened = False
        self._shift_closed = False
        self._register_id: str | None = None
        self._opening_float = 150.0
        self._cash_sales = 0.0
        self._card_sales = 0.0
        self._pending_setup: list[str] = []
        self._phase = "setup"
        self._delivery_index = 0

    # ------------------------------------------------------------------
    # BusinessSimulator contract
    # ------------------------------------------------------------------

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)

        # Reset deterministically so the same seed always produces the same run.
        self.id_gen = IdGenerator(self.rng)
        self.staff = StaffRoster(self.id_gen, self.rng, self.faker)
        self.menu = MenuCatalog(self.id_gen, self.rng, self.faker)
        self.members = ActorPool(
            self.id_gen,
            "member",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.tables = {
            table_id: {
                "table_id": table_id,
                "capacity": capacity,
                "occupied": False,
                "party_size": 0,
            }
            for table_id, capacity in self._TABLES
        }

        self.recipe_book = RecipeBook()
        self.ledger = InventoryLedger(self.rng, self.recipe_book)
        self._register_ingredients()

        self.order_machine = TransitionModel(
            {
                "new": ("fired",),
                "fired": ("preparing", "ready"),
                "preparing": ("ready",),
                "ready": ("paid",),
            }
        )
        self.ticket_machine = TransitionModel(
            {
                "new": ("fired",),
                "fired": ("ready",),
            }
        )

        self.catalog = WeightedEventCatalog(self.rng, self.scheduler)
        self._build_catalog()

        self.orders = []
        self.tickets = []
        self._shift_opened = False
        self._shift_closed = False
        self._register_id = self.id_gen.next("reg")
        self._opening_float = self.config.initial_state_overrides.get(
            "opening_float", 150.0
        )
        self._cash_sales = 0.0
        self._card_sales = 0.0
        self._pending_setup = []
        self._phase = "setup"
        self._delivery_index = 0

        self._enqueue_setup()
        self._seed_members()

    def available_event_types(self) -> list[str]:
        return [
            "shift_opened",
            "staff_clocked_in",
            "menu_item_added",
            "supplier_delivery_received",
            "table_occupied",
            "order_taken",
            "item_fired_to_kitchen",
            "item_prepared",
            "order_paid",
            "loyalty_points_earned",
            "wastage_logged",
            "stock_count_recorded",
            "shift_closed",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        self._update_phase(clock)

        if self._pending_setup:
            event_type = self._pending_setup.pop(0)
            return self._emit(event_type, clock)

        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "wastage_logged"
        return self._emit(event_type, clock)

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "shift_opened": self._shift_opened,
            "shift_closed": self._shift_closed,
            "staff_count": len(self.staff.all()),
            "menu_item_count": len(self.menu.all()),
            "ingredient_count": self.ledger.stock_count() if self.ledger else 0,
            "table_occupied_count": sum(
                1 for t in self.tables.values() if t["occupied"]
            ),
            "order_count": len(self.orders),
            "orders_by_status": self._orders_by_status(),
            "member_count": len(self.members.all()),
            "waste_total": self.ledger.waste_total() if self.ledger else 0.0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_ingredients(self) -> None:
        for ingredient_id, name, unit, unit_cost in self._INGREDIENTS:
            self.ledger.register_ingredient(
                ingredient_id=ingredient_id,
                name=name,
                unit=unit,
                unit_cost=unit_cost,
            )

    def _build_catalog(self) -> None:
        def has_opened(ctx: "CafeSimulator") -> bool:
            return ctx._shift_opened

        def in_service(ctx: "CafeSimulator") -> bool:
            return ctx._shift_opened and not ctx._shift_closed and ctx._phase != "setup"

        def staff_available(ctx: "CafeSimulator") -> bool:
            return any(not s["clocked_in"] for s in ctx.staff.all())

        def menu_incomplete(ctx: "CafeSimulator") -> bool:
            return len(ctx.menu.all()) < len(ctx._MENU)

        def deliveries_pending(ctx: "CafeSimulator") -> bool:
            return ctx._delivery_index < len(ctx._INGREDIENTS)

        def tables_available(ctx: "CafeSimulator") -> bool:
            return any(not t["occupied"] for t in ctx.tables.values())

        def menu_ready(ctx: "CafeSimulator") -> bool:
            return len(ctx.menu.all()) > 0

        def has_new_tickets(ctx: "CafeSimulator") -> bool:
            return any(t["status"] == "new" for t in ctx.tickets)

        def has_fired_tickets(ctx: "CafeSimulator") -> bool:
            return any(t["status"] == "fired" for t in ctx.tickets)

        def has_ready_orders(ctx: "CafeSimulator") -> bool:
            return any(o["status"] == "ready" for o in ctx.orders)

        def has_paid_orders(ctx: "CafeSimulator") -> bool:
            return any(o["status"] == "paid" for o in ctx.orders)

        def has_members(ctx: "CafeSimulator") -> bool:
            return len(ctx.members.all()) > 0

        def has_ingredients(ctx: "CafeSimulator") -> bool:
            return ctx.ledger.stock_count() > 0

        def closing_phase(ctx: "CafeSimulator") -> bool:
            return ctx._phase == "close"

        self.catalog.register(
            "staff_clocked_in",
            base_weight=5.0,
            guard=lambda ctx: has_opened(ctx)
            and not ctx._shift_closed
            and staff_available(ctx),
        )
        self.catalog.register(
            "menu_item_added",
            base_weight=5.0,
            guard=menu_incomplete,
        )
        self.catalog.register(
            "supplier_delivery_received",
            base_weight=5.0,
            guard=lambda ctx: menu_ready(ctx) and deliveries_pending(ctx),
        )
        self.catalog.register(
            "table_occupied",
            base_weight=10.0,
            guard=lambda ctx: in_service(ctx) and tables_available(ctx),
        )
        self.catalog.register(
            "order_taken",
            base_weight=15.0,
            guard=lambda ctx: in_service(ctx) and menu_ready(ctx),
        )
        self.catalog.register(
            "item_fired_to_kitchen",
            base_weight=12.0,
            guard=lambda ctx: in_service(ctx) and has_new_tickets(ctx),
        )
        self.catalog.register(
            "item_prepared",
            base_weight=12.0,
            guard=lambda ctx: in_service(ctx) and has_fired_tickets(ctx),
        )
        self.catalog.register(
            "order_paid",
            base_weight=10.0,
            guard=lambda ctx: in_service(ctx) and has_ready_orders(ctx),
        )
        self.catalog.register(
            "loyalty_points_earned",
            base_weight=5.0,
            guard=lambda ctx: in_service(ctx)
            and has_paid_orders(ctx)
            and has_members(ctx),
        )
        self.catalog.register(
            "wastage_logged",
            base_weight=3.0,
            guard=lambda ctx: has_ingredients(ctx) and not ctx._shift_closed,
        )
        self.catalog.register(
            "stock_count_recorded",
            base_weight=2.0,
            guard=lambda ctx: has_ingredients(ctx) and not ctx._shift_closed,
        )
        self.catalog.register(
            "shift_closed",
            base_weight=10.0,
            guard=lambda ctx: closing_phase(ctx) and not ctx._shift_closed,
        )

    def _enqueue_setup(self) -> None:
        self._pending_setup.append("shift_opened")
        # shift_opened clocks in the first staff member, so enqueue the rest.
        for _ in self._STAFF[1:]:
            self._pending_setup.append("staff_clocked_in")
        for _ in self._MENU:
            self._pending_setup.append("menu_item_added")
        for _ in self._INGREDIENTS:
            self._pending_setup.append("supplier_delivery_received")

    def _seed_members(self) -> None:
        initial_members = self.config.initial_state_overrides.get("initial_members", 5)
        for _ in range(initial_members):
            self.members.create(tier=self.rng.choice(["regular", "gold", "member"]))

    def _update_phase(self, clock: Clock) -> None:
        if self._phase == "setup" and not self._pending_setup:
            self._phase = "service"

        if self.config is None:
            return

        duration = self.config.duration_seconds
        elapsed = clock.elapsed_seconds()
        # Enter the closing phase a few ticks before the configured duration ends
        # so shift_closed has a chance to fire.
        if self._phase == "service" and elapsed >= max(0, duration - 4 * clock.tick_seconds):
            self._phase = "close"

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _emit_shift_opened(self, clock: Clock) -> dict[str, Any]:
        self._shift_opened = True
        staff = self._hire_or_get_first_staff()
        staff_id = staff["staff_id"]
        self.staff.clock_in(staff_id)
        return {
            "event_type": "shift_opened",
            "payload": {
                "register_id": self._register_id,
                "opened_at": clock.now.isoformat(),
                "opening_float": self._opening_float,
                "staff_id": staff_id,
            },
        }

    def _hire_or_get_first_staff(self) -> dict[str, Any]:
        if not self.staff.all():
            return self.staff.hire(role=self._STAFF[0][0], name=self._STAFF[0][1])
        return self.staff.all()[0]

    def _emit_staff_clocked_in(self, clock: Clock) -> dict[str, Any]:
        staff_index = len(self.staff.all())
        if staff_index < len(self._STAFF):
            role, name = self._STAFF[staff_index]
            staff = self.staff.hire(role=role, name=name)
        else:
            staff = self.staff.sample(clocked_in=False)
        self.staff.clock_in(staff["staff_id"])
        return {
            "event_type": "staff_clocked_in",
            "payload": {
                "staff_id": staff["staff_id"],
                "role": staff["role"],
                "clocked_in_at": clock.now.isoformat(),
            },
        }

    def _emit_menu_item_added(self, clock: Clock) -> dict[str, Any]:
        index = len(self.menu.all())
        name, category, price, components = self._MENU[index]
        item_id = f"item_{index + 1:04d}"
        self.recipe_book.add_recipe(item_id=item_id, components=components)
        item = self.menu.add_item(
            name=name,
            category=category,
            price=price,
            recipe=components,
        )
        return {
            "event_type": "menu_item_added",
            "payload": {
                "item_id": item["item_id"],
                "name": item["name"],
                "category": item["category"],
                "price": item["price"],
                "recipe": item["recipe"],
                "added_at": clock.now.isoformat(),
            },
        }

    def _emit_supplier_delivery_received(self, clock: Clock) -> dict[str, Any]:
        ingredient_id, name, unit, unit_cost = self._INGREDIENTS[
            self._delivery_index
        ]
        self._delivery_index += 1
        qty = self.rng.choice([10.0, 15.0, 20.0, 25.0])
        if unit == "each":
            qty = float(self.rng.choice([50, 100, 150, 200]))
        self.ledger.receive(ingredient_id, qty, unit_cost)
        delivery_id = self.id_gen.next("del")
        po_reference = self.id_gen.next("po")
        return {
            "event_type": "supplier_delivery_received",
            "payload": {
                "delivery_id": delivery_id,
                "supplier_id": self._SUPPLIER,
                "items": [
                    {
                        "ingredient_id": ingredient_id,
                        "name": name,
                        "qty": qty,
                        "unit": unit,
                        "unit_cost": unit_cost,
                    }
                ],
                "received_at": clock.now.isoformat(),
                "po_reference": po_reference,
            },
        }

    def _emit_table_occupied(self, clock: Clock) -> dict[str, Any]:
        available = [t for t in self.tables.values() if not t["occupied"]]
        table = self.rng.choice(available)
        party_size = self.rng.randint(1, table["capacity"])
        table["occupied"] = True
        table["party_size"] = party_size
        return {
            "event_type": "table_occupied",
            "payload": {
                "table_id": table["table_id"],
                "party_size": party_size,
                "occupied_at": clock.now.isoformat(),
                "channel": "dine_in",
            },
        }

    def _emit_order_taken(self, clock: Clock) -> dict[str, Any]:
        channel = self.rng.choice(["dine_in", "dine_in", "takeaway", "delivery"])
        table_id: str | None = None
        if channel == "dine_in":
            occupied = [t for t in self.tables.values() if t["occupied"]]
            if occupied:
                table_id = self.rng.choice(occupied)["table_id"]

        num_items = self.rng.randint(1, 3)
        items: list[dict[str, Any]] = []
        subtotal = 0.0
        for _ in range(num_items):
            menu_item = self.menu.sample()
            qty = self.rng.randint(1, 2)
            modifier = None
            line_total = round(menu_item["price"] * qty, 2)
            if self.rng.random() < 0.25 and menu_item["category"] == "coffee":
                modifier = {"name": "extra_shot", "price": 0.80}
                line_total = round(line_total + modifier["price"], 2)
            item_line = {
                "item_id": menu_item["item_id"],
                "name": menu_item["name"],
                "qty": qty,
                "modifiers": [modifier] if modifier else [],
                "line_total": line_total,
            }
            items.append(item_line)
            subtotal += line_total

        order_id = self.id_gen.next("ord")
        order = {
            "order_id": order_id,
            "channel": channel,
            "table_id": table_id,
            "items": items,
            "subtotal": round(subtotal, 2),
            "status": "new",
            "paid": False,
        }
        self.orders.append(order)
        self.order_machine.register(order_id, "new")

        for item_line in items:
            for _ in range(item_line["qty"]):
                ticket_id = self.id_gen.next("tkt")
                ticket = {
                    "ticket_id": ticket_id,
                    "order_id": order_id,
                    "item_id": item_line["item_id"],
                    "station": self._station_for(item_line["item_id"]),
                    "status": "new",
                }
                self.tickets.append(ticket)
                self.ticket_machine.register(ticket_id, "new")

        return {
            "event_type": "order_taken",
            "payload": {
                "order_id": order_id,
                "channel": channel,
                "table_id": table_id,
                "items": items,
                "taken_at": clock.now.isoformat(),
                "subtotal": order["subtotal"],
            },
        }

    def _station_for(self, item_id: str) -> str:
        item = next((i for i in self.menu.all() if i["item_id"] == item_id), None)
        if item is None:
            return "bar"
        if item["category"] in ("coffee", "cold"):
            return "bar"
        if item["category"] == "pastry":
            return "pastry"
        return "kitchen"

    def _emit_item_fired_to_kitchen(self, clock: Clock) -> dict[str, Any]:
        ticket = self.rng.choice([t for t in self.tickets if t["status"] == "new"])
        self.ticket_machine.transition(ticket["ticket_id"], "fired")
        ticket["status"] = "fired"
        ticket["fired_at"] = clock.now
        ticket["due_at"] = clock.now

        order = next(o for o in self.orders if o["order_id"] == ticket["order_id"])
        if all(t["status"] != "new" for t in self._tickets_for_order(order["order_id"])):
            self.order_machine.transition(order["order_id"], "fired")
            order["status"] = "fired"

        return {
            "event_type": "item_fired_to_kitchen",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "order_id": ticket["order_id"],
                "item_id": ticket["item_id"],
                "station": ticket["station"],
                "fired_at": clock.now.isoformat(),
                "due_at": ticket["due_at"].isoformat(),
            },
        }

    def _emit_item_prepared(self, clock: Clock) -> dict[str, Any]:
        ticket = self.rng.choice([t for t in self.tickets if t["status"] == "fired"])
        self.ledger.deduct_for_item(ticket["item_id"], qty=1)
        self.ticket_machine.transition(ticket["ticket_id"], "ready")
        ticket["status"] = "ready"
        ticket["prepared_at"] = clock.now

        order = next(o for o in self.orders if o["order_id"] == ticket["order_id"])
        if all(t["status"] == "ready" for t in self._tickets_for_order(order["order_id"])):
            self.order_machine.transition(order["order_id"], "ready")
            order["status"] = "ready"

        return {
            "event_type": "item_prepared",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "item_id": ticket["item_id"],
                "station": ticket["station"],
                "prepared_at": clock.now.isoformat(),
                "status": "ready",
            },
        }

    def _emit_order_paid(self, clock: Clock) -> dict[str, Any]:
        order = self.rng.choice([o for o in self.orders if o["status"] == "ready"])
        self.order_machine.transition(order["order_id"], "paid")
        order["status"] = "paid"

        method = self.rng.choice(["card", "cash", "mobile"])
        tip_rate = self.rng.choice([0.0, 0.05, 0.1, 0.15, 0.2])
        tip = round(order["subtotal"] * tip_rate, 2)
        tax_amount = compute_tax(order["subtotal"])
        total = round(order["subtotal"] + tax_amount + tip, 2)
        payment_id = self.id_gen.next("pay")
        order["payment_id"] = payment_id
        order["total"] = total
        order["tip"] = tip
        order["tax_amount"] = tax_amount
        order["method"] = method
        order["paid"] = True

        if method == "cash":
            self._cash_sales += total
        else:
            self._card_sales += total

        # Free the table if it was dine-in.
        if order["table_id"]:
            table = self.tables.get(order["table_id"])
            if table:
                table["occupied"] = False
                table["party_size"] = 0

        return {
            "event_type": "order_paid",
            "payload": {
                "payment_id": payment_id,
                "order_id": order["order_id"],
                "amount": order["subtotal"],
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "tip": tip,
                "total": total,
                "method": method,
                "paid_at": clock.now.isoformat(),
            },
        }

    def _emit_loyalty_points_earned(self, clock: Clock) -> dict[str, Any]:
        order = self.rng.choice([o for o in self.orders if o["status"] == "paid"])
        member = self.members.sample()
        points = int(order.get("total", order["subtotal"]))
        return {
            "event_type": "loyalty_points_earned",
            "payload": {
                "member_id": member.actor_id,
                "order_id": order["order_id"],
                "points": points,
                "tier": member.attributes.get("tier", "regular"),
                "earned_at": clock.now.isoformat(),
            },
        }

    def _emit_wastage_logged(self, clock: Clock) -> dict[str, Any]:
        ingredient_id = self.rng.choice(self.ledger.all_ingredients())
        reason = self.rng.choice(["spoiled", "overproduction", "dropped", "expired"])
        qty = round(self.rng.uniform(0.1, 1.0), 2)
        if self.ledger._stock[ingredient_id]["unit"] == "each":
            qty = float(self.rng.randint(1, 5))
        waste = self.ledger.record_wastage(ingredient_id, qty, reason)
        waste_id = self.id_gen.next("wst")
        return {
            "event_type": "wastage_logged",
            "payload": {
                "waste_id": waste_id,
                "ingredient_id": waste["ingredient_id"],
                "item_id": None,
                "reason": waste["reason"],
                "qty": waste["qty"],
                "unit": waste["unit"],
                "estimated_cost": waste["estimated_cost"],
                "logged_at": clock.now.isoformat(),
            },
        }

    def _emit_stock_count_recorded(self, clock: Clock) -> dict[str, Any]:
        ingredient_id = self.rng.choice(self.ledger.all_ingredients())
        expected = self.ledger.qty(ingredient_id)
        # Actual is expected plus a small deterministic variance.
        variance = round(self.rng.choice([-0.5, -0.2, 0.0, 0.1, 0.3]), 2)
        if self.ledger._stock[ingredient_id]["unit"] == "each":
            variance = float(self.rng.choice([-2, -1, 0, 1, 2]))
        actual = max(0.0, round(expected + variance, 2))
        count = self.ledger.record_stock_count(ingredient_id, actual)
        return {
            "event_type": "stock_count_recorded",
            "payload": {
                "ingredient_id": count["ingredient_id"],
                "expected_qty": count["expected_qty"],
                "actual_qty": count["actual_qty"],
                "variance": count["variance"],
                "unit": count["unit"],
                "counted_at": clock.now.isoformat(),
            },
        }

    def _emit_shift_closed(self, clock: Clock) -> dict[str, Any]:
        self._shift_closed = True
        closing_float = self._opening_float + self._cash_sales
        total_sales = self._cash_sales + self._card_sales
        expected_float = self._opening_float + self._cash_sales
        # Small deterministic discrepancy: round to nearest 0.05.
        discrepancy = round(self.rng.choice([0.0, 0.0, 0.05, -0.05, 0.1]), 2)
        if self.rng.random() < 0.85:
            discrepancy = 0.0
        closing_float = round(expected_float + discrepancy, 2)

        # Clock remaining staff out so the state snapshot is internally consistent.
        for staff in self.staff.all():
            staff["clocked_in"] = False

        return {
            "event_type": "shift_closed",
            "payload": {
                "register_id": self._register_id,
                "closed_at": clock.now.isoformat(),
                "opening_float": self._opening_float,
                "closing_float": closing_float,
                "cash_sales": round(self._cash_sales, 2),
                "card_sales": round(self._card_sales, 2),
                "total_sales": round(total_sales, 2),
                "discrepancy": discrepancy,
                "waste_total": self.ledger.waste_total(),
            },
        }

    def _tickets_for_order(self, order_id: str) -> list[dict[str, Any]]:
        return [t for t in self.tickets if t["order_id"] == order_id]

    def _orders_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for order in self.orders:
            counts[order["status"]] = counts.get(order["status"], 0) + 1
        return counts
