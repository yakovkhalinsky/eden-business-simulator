"""Codecs for genuine full-state snapshots (restorable-state S1).

The framework pieces every business needs to persist *complete* state —
as opposed to the historical summary ``state_snapshot()`` — live here:

- **RNG state** (R6: raw JSON, never pickle). ``random.Random.getstate()``
  returns ``(version, (625 ints), gauss_next)``; JSON turns the tuples
  into lists, and ``setstate()`` rejects lists ("state vector must be a
  tuple"), so the decoder converts the vector back to a tuple. With that
  one conversion the round-trip is bit-exact: a restored RNG produces
  exactly the draws an untouched twin would have produced.
- **Faker state**: Faker draws from its own ``random.Random`` (reachable
  as ``faker.random``). It round-trips through the same codec. Faker
  internals are not a public API, so :func:`faker_random` fails loudly
  if the attribute ever moves, rather than silently dropping state.
- **Datetimes**: entity state contains real ``datetime``/``date``
  values (clinic appointment times are the canonical case — arithmetic
  is performed on them, so they must come back as datetimes, not
  strings). They are encoded as single-key tagged dicts and decoded
  back to real objects. This is deliberately *not* a blanket
  stringification: the storage layer's ``json.dumps(..., default=str)``
  silently stringifies stray datetimes, which is exactly the corruption
  this codec exists to prevent. Because the codec's output is plain
  JSON types (dicts, lists, strings, numbers, ``None``), the full-state
  section passes through every storage backend's JSON round-trip
  untouched by that lossy default.

Everything this module produces is JSON-safe by construction.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from typing import Any

# Version of the ``full_state`` section inside a snapshot's ``data``
# (see BusinessSimulator.full_state_snapshot). Bump on any shape change;
# decoders refuse anything they do not recognise rather than misreading.
FULL_STATE_FORMAT_VERSION = 2

# Version of the runner-written snapshot *envelope* (the dict stored
# under the "latest" key: seed/business_type/data plus, from this
# version on, a "clock" section). Envelopes without this key are
# legacy (pre-restorable-state) summary snapshots — see R2.
SNAPSHOT_FORMAT_VERSION = 2

_DATETIME_TAG = "__eden_datetime__"
_DATE_TAG = "__eden_date__"


class FullStateVersionError(ValueError):
    """A full-state section declares a format this build cannot read.

    Raised instead of guessing: silently misreading state would produce
    a resumed run that diverges without any signal — the failure mode
    the restorable-state project exists to remove.
    """


# ----------------------------------------------------------------------
# RNG / Faker state
# ----------------------------------------------------------------------


def encode_random_state(state: tuple[Any, ...]) -> list[Any]:
    """Encode a ``random.Random.getstate()`` tuple as JSON-safe data."""
    version, vector, gauss_next = state
    return [version, list(vector), gauss_next]


def decode_random_state(data: list[Any]) -> tuple[Any, ...]:
    """Inverse of :func:`encode_random_state`.

    ``setstate()`` requires the state vector to be a tuple, so the
    JSON-decoded list is converted back here.
    """
    version, vector, gauss_next = data
    return (version, tuple(vector), gauss_next)


def faker_random(faker: Any) -> random.Random:
    """Return the ``random.Random`` a Faker instance draws from.

    Fails loudly (rather than silently skipping faker state) if a
    future Faker version moves the attribute.
    """
    rng = getattr(faker, "random", None)
    if not isinstance(rng, random.Random):
        raise RuntimeError(
            "Cannot locate Faker's internal random.Random "
            f"(found {type(rng).__name__}); faker state cannot be "
            "captured and full-state restore would diverge."
        )
    return rng


# ----------------------------------------------------------------------
# Datetime-safe entity state
# ----------------------------------------------------------------------


def encode_datetimes(value: Any) -> Any:
    """Recursively encode datetime/date values as tagged dicts.

    Dicts and lists/tuples are walked; tuples come back as lists (JSON
    has no tuple type — state documents use lists). All other values
    pass through unchanged. The output is JSON-safe.
    """
    if isinstance(value, datetime):
        return {_DATETIME_TAG: value.isoformat()}
    if isinstance(value, date):
        return {_DATE_TAG: value.isoformat()}
    if isinstance(value, dict):
        return {key: encode_datetimes(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_datetimes(item) for item in value]
    return value


def decode_datetimes(value: Any) -> Any:
    """Inverse of :func:`encode_datetimes`.

    A dict whose *only* key is one of the tags is decoded back to a
    ``datetime``/``date``; every other structure is walked through.
    """
    if isinstance(value, dict):
        if set(value) == {_DATETIME_TAG}:
            return datetime.fromisoformat(value[_DATETIME_TAG])
        if set(value) == {_DATE_TAG}:
            return date.fromisoformat(value[_DATE_TAG])
        return {key: decode_datetimes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_datetimes(item) for item in value]
    return value
