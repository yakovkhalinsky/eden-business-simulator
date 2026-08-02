"""Deterministic ID generation seeded from a simulator run."""

from __future__ import annotations

import random


class IdGenerator:
    """Produces deterministic, human-readable IDs like ``ord_0001``.

    IDs are derived from an internal counter per prefix.  Because counters only
    advance when ``next`` is called, the generated IDs are reproducible as long
    as the simulator performs the same sequence of operations for the same
    seed.
    """

    def __init__(self, rng_or_seed: random.Random | int) -> None:
        if isinstance(rng_or_seed, int):
            self._rng = random.Random(rng_or_seed)
        else:
            self._rng = rng_or_seed
        self._counters: dict[str, int] = {}

    def next(self, prefix: str, width: int = 4) -> str:
        """Return the next ID for ``prefix`` and advance its counter."""
        count = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = count
        return f"{prefix}_{count:0{width}d}"

    def slug(self, prefix: str, *parts: str, width: int = 2) -> str:
        """Return a compound ID such as ``station_bar_01``.

        The base slug is deterministic from ``parts`` but the numeric suffix
        still advances a dedicated counter so repeated calls for the same base
        are ordered.
        """
        base = "_".join([prefix, *parts])
        return self.next(base, width=width)
