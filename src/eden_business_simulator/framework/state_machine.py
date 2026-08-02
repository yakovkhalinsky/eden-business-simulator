"""Generic state-machine helper for entity lifecycle transitions."""

from __future__ import annotations

from typing import Any


class TransitionModel:
    """Tracks per-entity states and allowed transitions.

    A simulator registers an entity with an initial state and then advances it
    through allowed transitions.  Calling ``transition`` only succeeds when the
    target state is in the allowed set for the current state.
    """

    def __init__(self, allowed: dict[str, tuple[str, ...]]) -> None:
        """
        Args:
            allowed: mapping ``current_state -> (next_states, ...)``.
        """
        self._allowed = {state: tuple(targets) for state, targets in allowed.items()}
        self._states: dict[str, str] = {}

    def register(self, entity_id: str, initial_state: str) -> None:
        self._states[entity_id] = initial_state

    def get(self, entity_id: str) -> str | None:
        return self._states.get(entity_id)

    def can_transition(self, entity_id: str, target_state: str) -> bool:
        current = self._states.get(entity_id)
        if current is None:
            return False
        return target_state in self._allowed.get(current, ())

    def transition(self, entity_id: str, target_state: str) -> bool:
        """Attempt a transition.  Returns ``True`` if it was allowed."""
        if not self.can_transition(entity_id, target_state):
            return False
        self._states[entity_id] = target_state
        return True

    def remove(self, entity_id: str) -> None:
        self._states.pop(entity_id, None)

    def entities_in_state(self, state: str) -> list[str]:
        return [eid for eid, st in self._states.items() if st == state]

    def snapshot(self) -> dict[str, Any]:
        return dict(self._states)
