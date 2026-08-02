"""Tests for reusable actor/entity helpers."""

import random

from faker import Faker

from eden_business_simulator.framework.actors import (
    ActorPool,
    MenuCatalog,
    RecipeComponent,
    StaffRoster,
)
from eden_business_simulator.framework.ids import IdGenerator


def _deps(seed: int = 42):
    rng = random.Random(seed)
    faker = Faker()
    faker.seed_instance(seed)
    id_gen = IdGenerator(rng)
    return rng, faker, id_gen


def test_actor_pool_deterministic_ids():
    rng, faker, id_gen = _deps()
    pool = ActorPool(id_gen, "customer", faker, rng)
    a = pool.create()
    b = pool.create()
    assert a.actor_id == "customer_0001"
    assert b.actor_id == "customer_0002"
    assert len(pool.all()) == 2


def test_actor_pool_sample():
    rng, faker, id_gen = _deps()
    pool = ActorPool(id_gen, "customer", faker, rng)
    for _ in range(5):
        pool.create()
    sample = pool.sample()
    assert sample.actor_id in {a.actor_id for a in pool.all()}


def test_staff_roster_roles():
    rng, faker, id_gen = _deps()
    roster = StaffRoster(id_gen, rng, faker)
    manager = roster.hire(role="manager", name="Avery")
    barista = roster.hire(role="barista", name="Jordan")
    assert manager["staff_id"] == "staff_0001"
    assert barista["role"] == "barista"
    assert not manager["clocked_in"]
    roster.clock_in(manager["staff_id"])
    assert manager["clocked_in"]


def test_staff_roster_sample_filter():
    rng, faker, id_gen = _deps(seed=7)
    roster = StaffRoster(id_gen, rng, faker)
    for role in ("barista", "server", "manager"):
        roster.hire(role=role)
    roster.clock_in("staff_0001")
    out = roster.sample(clocked_in=False)
    assert out["staff_id"] != "staff_0001"


def test_menu_catalog_recipe():
    rng, faker, id_gen = _deps()
    catalog = MenuCatalog(id_gen, rng, faker)
    item = catalog.add_item(
        name="oat flat white",
        category="coffee",
        price=5.5,
        recipe=[
            RecipeComponent("ing_001", "espresso", 0.036, "kg"),
            RecipeComponent("ing_002", "oat_milk", 0.24, "l"),
        ],
    )
    assert item["item_id"] == "item_0001"
    assert item["price"] == 5.5
    assert len(item["recipe"]) == 2
    assert item["recipe"][0]["ingredient_id"] == "ing_001"


def test_menu_catalog_sample_by_category():
    rng, faker, id_gen = _deps(seed=3)
    catalog = MenuCatalog(id_gen, rng, faker)
    catalog.add_item("espresso", "coffee", 3.0)
    catalog.add_item("croissant", "pastry", 4.5)
    coffee = catalog.sample(category="coffee")
    assert coffee["category"] == "coffee"
