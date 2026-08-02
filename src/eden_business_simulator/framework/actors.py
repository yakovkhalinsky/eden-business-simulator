"""Lightweight actor/entity pools for staff, customers, menu items, etc."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from faker import Faker

from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.framework.inventory import RecipeComponent


@dataclass
class Actor:
    """A generic participant or entity in a simulation."""

    actor_id: str
    actor_type: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)


class ActorPool:
    """Generic pool of actors with deterministic ID generation."""

    def __init__(
        self,
        id_gen: IdGenerator,
        actor_type: str,
        faker: Faker,
        rng: random.Random,
        name_factory: Callable[[Faker, random.Random], str] | None = None,
    ) -> None:
        self.id_gen = id_gen
        self.actor_type = actor_type
        self.faker = faker
        self.rng = rng
        self.name_factory = name_factory or (lambda faker, rng: faker.name())
        self._actors: list[Actor] = []

    def create(self, **attributes: Any) -> Actor:
        actor_id = self.id_gen.next(self.actor_type)
        actor = Actor(
            actor_id=actor_id,
            actor_type=self.actor_type,
            name=self.name_factory(self.faker, self.rng),
            attributes=attributes,
        )
        self._actors.append(actor)
        return actor

    def add(self, actor: Actor) -> Actor:
        self._actors.append(actor)
        return actor

    def all(self) -> list[Actor]:
        return list(self._actors)

    def get(self, actor_id: str) -> Actor | None:
        for actor in self._actors:
            if actor.actor_id == actor_id:
                return actor
        return None

    def sample(self) -> Actor:
        return self.rng.choice(self._actors)


class StaffRoster:
    """Pool of staff members with roles and clock-in state."""

    _DEFAULT_ROLES = ("barista", "server", "manager", "chef", "cashier")

    def __init__(
        self,
        id_gen: IdGenerator,
        rng: random.Random,
        faker: Faker,
        roles: tuple[str, ...] | None = None,
    ) -> None:
        self.id_gen = id_gen
        self.rng = rng
        self.faker = faker
        self.roles = roles or self._DEFAULT_ROLES
        self._staff: list[dict[str, Any]] = []

    def hire(self, role: str | None = None, name: str | None = None) -> dict[str, Any]:
        if role is None:
            role = self.rng.choice(self.roles)
        staff_id = self.id_gen.next("staff")
        staff = {
            "staff_id": staff_id,
            "name": name or self.faker.name(),
            "role": role,
            "clocked_in": False,
        }
        self._staff.append(staff)
        return staff

    def all(self) -> list[dict[str, Any]]:
        return list(self._staff)

    def clock_in(self, staff_id: str) -> dict[str, Any] | None:
        for staff in self._staff:
            if staff["staff_id"] == staff_id:
                staff["clocked_in"] = True
                return staff
        return None

    def sample(self, *, clocked_in: bool | None = None) -> dict[str, Any]:
        pool = self._staff
        if clocked_in is not None:
            pool = [s for s in pool if s["clocked_in"] == clocked_in]
        if not pool:
            raise ValueError("No staff members match the requested filter")
        return self.rng.choice(pool)


class MenuCatalog:
    """Menu items with recipes and deterministic ID generation."""

    def __init__(
        self,
        id_gen: IdGenerator,
        rng: random.Random,
        faker: Faker,
    ) -> None:
        self.id_gen = id_gen
        self.rng = rng
        self.faker = faker
        self._items: list[dict[str, Any]] = []

    def add_item(
        self,
        name: str,
        category: str,
        price: float,
        recipe: list[RecipeComponent] | None = None,
        **attributes: Any,
    ) -> dict[str, Any]:
        item_id = self.id_gen.next("item")
        item = {
            "item_id": item_id,
            "name": name,
            "category": category,
            "price": round(price, 2),
            "recipe": [
                {
                    "ingredient_id": c.ingredient_id,
                    "name": c.name,
                    "qty": c.qty,
                    "unit": c.unit,
                }
                for c in (recipe or [])
            ],
            **attributes,
        }
        self._items.append(item)
        return item

    def all(self) -> list[dict[str, Any]]:
        return list(self._items)

    def sample(self, category: str | None = None) -> dict[str, Any]:
        pool = self._items
        if category is not None:
            pool = [i for i in pool if i["category"] == category]
        if not pool:
            raise ValueError("No menu items match the requested filter")
        return self.rng.choice(pool)
