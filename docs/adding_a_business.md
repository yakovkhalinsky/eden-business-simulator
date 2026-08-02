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
