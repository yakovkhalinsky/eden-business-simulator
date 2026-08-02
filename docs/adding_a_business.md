# Adding a new business simulator

1. Create a module under `src/eden_business_simulator/businesses/` (for example `healthcare.py`).
2. Subclass `BusinessSimulator` and set `business_type` to a short slug.
3. Implement `configure`, `initialize`, `available_event_types`, `next_event`, and `state_snapshot`.
4. Import and register the class in `src/eden_business_simulator/businesses/__init__.py`.
5. Add tests under `tests/` and update this document if conventions change.

## Interface contract

```python
class MySimulator(BusinessSimulator):
    business_type = "my_domain"

    def configure(self, config: SimulatorConfig) -> None: ...
    def initialize(self, seed: int) -> None: ...
    def available_event_types(self) -> list[str]: ...
    def next_event(self, clock: Clock) -> dict[str, Any]: ...
    def state_snapshot(self) -> dict[str, Any]: ...
```

`next_event` must return a dict with `event_type` and `payload`. The runner wraps it in an `EventEnvelope` and writes it through the selected output adapter.

## Determinism

Use the provided `seed` to initialize `random.Random` and `faker.Faker` instances. Never rely on global random state so that a run is reproducible when given the same seed.

## Framework helpers

The `eden_business_simulator.framework` package contains reusable components so that new business domains do not have to re-implement common patterns from scratch.  The cafe simulator is the reference implementation (`src/eden_business_simulator/businesses/cafe.py`).

| Module | Helper | Purpose |
|--------|--------|---------|
| `framework.ids` | `IdGenerator` | Deterministic IDs such as `ord_0001` per prefix. |
| `framework.actors` | `ActorPool`, `StaffRoster`, `MenuCatalog` | Deterministic entity pools for customers, staff, and menu items with recipes. |
| `framework.inventory` | `RecipeBook`, `InventoryLedger` | Recipe/BOM support, stock receipts, auto-deduction on preparation, wastage, and stock counts. |
| `framework.scheduler` | `DaypartScheduler`, `Daypart` | Vary event weights by simulated clock hour (e.g. breakfast/lunch rushes, close). |
| `framework.state_machine` | `TransitionModel` | Allowed lifecycle transitions for orders, tickets, reservations, etc. |
| `framework.catalog` | `WeightedEventCatalog` | Declarative event weights with state guards and time-of-day modifiers. |

### Example: a minimal simulator using helpers

```python
import random
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.framework.scheduler import DaypartScheduler
from eden_business_simulator.models import Clock


class MySimulator(BusinessSimulator):
    business_type = "my_domain"

    def __init__(self) -> None:
        self.rng = random.Random()
        self.faker = Faker()
        self.id_gen = IdGenerator(self.rng)
        self.catalog = WeightedEventCatalog(self.rng)

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)
        self.id_gen = IdGenerator(self.rng)
        self.catalog = WeightedEventCatalog(self.rng)
        self.catalog.register("thing_happened", base_weight=10.0)

    def available_event_types(self) -> list[str]:
        return ["thing_happened"]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        return {
            "event_type": event_type,
            "payload": {
                "thing_id": self.id_gen.next("thing"),
                "at": clock.now.isoformat(),
            },
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {}
```

Use `DaypartScheduler` when event likelihoods should vary by simulated hour, and combine it with `WeightedEventCatalog` by passing the scheduler into the catalog constructor.
