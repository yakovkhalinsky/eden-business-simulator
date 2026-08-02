"""Tests for the inventory ledger and recipe book."""

import random

from eden_business_simulator.framework.inventory import (
    InventoryLedger,
    RecipeBook,
    RecipeComponent,
)


def test_recipe_book_lookup():
    book = RecipeBook()
    book.add_recipe(
        "item_0001",
        [
            RecipeComponent("ing_001", "espresso", 0.036, "kg"),
            RecipeComponent("ing_002", "oat_milk", 0.24, "l"),
        ],
    )
    components = book.recipe_for("item_0001")
    assert len(components) == 2
    assert components[0].name == "espresso"


def test_ledger_receive_and_deduct():
    rng = random.Random(42)
    ledger = InventoryLedger(rng)
    ledger.register_ingredient("ing_002", "oat_milk", "l", 1.8)
    ledger.receive("ing_002", 20.0, 1.8)
    assert ledger.qty("ing_002") == 20.0

    book = RecipeBook()
    book.add_recipe("item_0001", [RecipeComponent("ing_002", "oat_milk", 0.24, "l")])
    ledger.set_recipe_book(book)

    ledger.deduct_for_item("item_0001", qty=1)
    assert ledger.qty("ing_002") == 19.76


def test_ledger_wastage():
    rng = random.Random(42)
    ledger = InventoryLedger(rng)
    ledger.register_ingredient("ing_002", "oat_milk", "l", 1.8)
    ledger.receive("ing_002", 10.0, 1.8)

    waste = ledger.record_wastage("ing_002", 0.5, "spoiled")
    assert waste["qty"] == 0.5
    assert waste["estimated_cost"] == 0.9
    assert ledger.qty("ing_002") == 9.5


def test_ledger_stock_count_variance():
    rng = random.Random(42)
    ledger = InventoryLedger(rng)
    ledger.register_ingredient("ing_001", "espresso", "kg", 18.0)
    ledger.receive("ing_001", 5.0, 18.0)

    count = ledger.record_stock_count("ing_001", 4.8)
    assert count["expected_qty"] == 5.0
    assert count["actual_qty"] == 4.8
    assert count["variance"] == -0.2
    assert ledger.qty("ing_001") == 4.8


def test_ledger_waste_total_accumulates():
    rng = random.Random(42)
    ledger = InventoryLedger(rng)
    ledger.register_ingredient("ing_001", "espresso", "kg", 10.0)
    ledger.receive("ing_001", 1.0, 10.0)
    ledger.record_wastage("ing_001", 0.1, "dropped")
    ledger.record_wastage("ing_001", 0.2, "expired")
    assert ledger.waste_total() == 3.0
