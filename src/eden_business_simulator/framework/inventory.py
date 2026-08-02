"""Inventory ledger with recipe / BOM support and auto-deduction on prep."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass
class RecipeComponent:
    """One ingredient line in a recipe / bill of materials."""

    ingredient_id: str
    name: str
    qty: float
    unit: str


class RecipeBook:
    """Maps menu items to their ingredient requirements."""

    def __init__(self) -> None:
        self._recipes: dict[str, list[RecipeComponent]] = {}

    def add_recipe(
        self,
        item_id: str,
        components: list[RecipeComponent],
    ) -> None:
        self._recipes[item_id] = list(components)

    def recipe_for(self, item_id: str) -> list[RecipeComponent]:
        return list(self._recipes.get(item_id, []))

    def ingredients_for(self, item_id: str) -> list[str]:
        return [c.ingredient_id for c in self.recipe_for(item_id)]


class InventoryLedger:
    """Tracks ingredient stock, recipe consumption, wastage, and stock counts."""

    def __init__(
        self,
        rng: random.Random,
        recipe_book: RecipeBook | None = None,
    ) -> None:
        self.rng = rng
        self.recipe_book = recipe_book or RecipeBook()
        self._stock: dict[str, dict[str, Any]] = {}
        self._waste_total: float = 0.0

    def set_recipe_book(self, recipe_book: RecipeBook) -> None:
        self.recipe_book = recipe_book

    def register_ingredient(
        self,
        ingredient_id: str,
        name: str,
        unit: str,
        unit_cost: float,
        initial_qty: float = 0.0,
    ) -> dict[str, Any]:
        if ingredient_id not in self._stock:
            self._stock[ingredient_id] = {
                "ingredient_id": ingredient_id,
                "name": name,
                "unit": unit,
                "unit_cost": unit_cost,
                "qty": initial_qty,
            }
        return self._stock[ingredient_id]

    def receive(
        self,
        ingredient_id: str,
        qty: float,
        unit_cost: float,
    ) -> dict[str, Any]:
        stock = self._stock.get(ingredient_id)
        if stock is None:
            raise KeyError(f"Unknown ingredient: {ingredient_id}")
        stock["qty"] += qty
        stock.setdefault("unit_cost", unit_cost)
        return stock

    def deduct_for_item(
        self,
        item_id: str,
        qty: int = 1,
    ) -> list[dict[str, Any]]:
        """Deduct recipe quantities from stock.  Returns the movements made."""
        movements: list[dict[str, Any]] = []
        for component in self.recipe_book.recipe_for(item_id):
            stock = self._stock.get(component.ingredient_id)
            if stock is None:
                continue
            consumed = round(component.qty * qty, 6)
            stock["qty"] = max(0.0, round(stock["qty"] - consumed, 6))
            movements.append(
                {
                    "ingredient_id": component.ingredient_id,
                    "qty": consumed,
                    "unit": component.unit,
                    "unit_cost": stock.get("unit_cost", 0.0),
                }
            )
        return movements

    def record_wastage(
        self,
        ingredient_id: str,
        qty: float,
        reason: str,
    ) -> dict[str, Any]:
        stock = self._stock.get(ingredient_id)
        if stock is None:
            raise KeyError(f"Unknown ingredient: {ingredient_id}")
        actual_qty = min(qty, stock["qty"])
        stock["qty"] = round(stock["qty"] - actual_qty, 6)
        cost = round(actual_qty * stock.get("unit_cost", 0.0), 2)
        self._waste_total += cost
        return {
            "ingredient_id": ingredient_id,
            "qty": actual_qty,
            "unit": stock["unit"],
            "reason": reason,
            "estimated_cost": cost,
        }

    def record_stock_count(
        self,
        ingredient_id: str,
        actual_qty: float,
    ) -> dict[str, Any]:
        stock = self._stock.get(ingredient_id)
        if stock is None:
            raise KeyError(f"Unknown ingredient: {ingredient_id}")
        expected = stock["qty"]
        variance = round(actual_qty - expected, 6)
        stock["qty"] = actual_qty
        return {
            "ingredient_id": ingredient_id,
            "expected_qty": expected,
            "actual_qty": actual_qty,
            "variance": variance,
            "unit": stock["unit"],
        }

    def stock_count(self) -> int:
        return len(self._stock)

    def qty(self, ingredient_id: str) -> float:
        stock = self._stock.get(ingredient_id)
        if stock is None:
            raise KeyError(f"Unknown ingredient: {ingredient_id}")
        return stock["qty"]

    def all_ingredients(self) -> list[str]:
        return list(self._stock.keys())

    def waste_total(self) -> float:
        return round(self._waste_total, 2)
