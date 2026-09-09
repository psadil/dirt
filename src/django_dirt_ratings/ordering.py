"""Review-ordering strategies — how the next image to review is chosen.

Each strategy encapsulates its own ``order_by`` on a per-step ``Image``
queryset and self-registers by its :class:`~models.ReviewStrategy` key (the same
registry idiom the ingest ``StepSpec`` uses, but class-based via
``__init_subclass__``; cf. Effective Python, "Register Class Existence with
``__init_subclass__``"). Adding a strategy is a new subclass, its ``ReviewStrategy``
key, and a covering index for its order (see ``Image.Meta.indexes``).

Why breadth-first is the backbone. Submitting a review is what advances the queue:
it increments ``Image.n_reviews`` so a reviewed image sinks. ``priority`` is static
(reviewing never changes it), so it can only ever *re-rank within* a review-depth
band — never be the primary key — or the reviewer would ping-pong between the two
worst images forever. The same holds for any other static key, such as a view's
position within its scan (``AnomalySweep``). On the first pass (everything at
``n_reviews == 0``) the whole step is ordered worst-first; then it naturally deepens.
"""

from __future__ import annotations

import abc
from typing import ClassVar

from django.db import models as dm

from django_dirt_ratings import models


class OrderingStrategy(abc.ABC):
    """Base class + registry for review-ordering strategies."""

    key: ClassVar[str]
    _registry: ClassVar[dict[str, type[OrderingStrategy]]] = {}

    def __init_subclass__(cls, /, key: str, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        cls.key = key
        OrderingStrategy._registry[key] = cls

    @classmethod
    def build(cls, key: str) -> OrderingStrategy:
        """Construct the strategy for a ``ReviewStrategy`` key (as stored on a Session)."""
        try:
            subclass = cls._registry[str(key)]
        except KeyError as e:
            raise ValueError(
                f"unknown ordering strategy {key!r}; have {sorted(cls._registry)}"
            ) from e
        return subclass()

    @abc.abstractmethod
    def order(self, qs: dm.QuerySet[models.Image]) -> dm.QuerySet[models.Image]:
        """Order ``qs`` so ``.first()`` is the next image to serve."""
        raise NotImplementedError


class BreadthFirst(OrderingStrategy, key=models.ReviewStrategy.BREADTH_FIRST.value):
    """Fewest reviews first — the default. Backed by the ``image_next`` index."""

    def order(self, qs):
        return qs.order_by("n_reviews", "id")


class AnomalyFirst(OrderingStrategy, key=models.ReviewStrategy.ANOMALY_FIRST.value):
    """Breadth backbone; most-atypical (highest ``priority``) re-ranked within a band.

    NULL priority (no measure) sorts after scored images. Backed by the
    ``image_priority`` index — a single covering seek, no sort.
    """

    def order(self, qs):
        return qs.order_by("n_reviews", dm.F("priority").desc(nulls_last=True), "id")


class AnomalySweep(OrderingStrategy, key=models.ReviewStrategy.ANOMALY_SWEEP.value):
    """``anomaly_first``, but one view of every scan per pass.

    A scan renders into several views — the same ``(slice, display)`` positions
    for every scan of a step — and ``priority`` is a property of the scan, so
    ``anomaly_first`` serves all of the worst scan's views back to back. Here a
    view's *position* ranks above ``priority``: within a review-depth band the
    queue walks cut 0 of every scan (worst first, one orientation at a time), then
    cut 1, ... so every scan gets one look before any gets a second, and only once
    every view has been seen does the next band deepen. Position is as static as
    priority, so ``n_reviews`` stays the backbone. With no priorities at all this
    is breadth-first swept one view at a time. Backed by the ``image_sweep`` index
    — a single covering seek, no sort.
    """

    def order(self, qs):
        return qs.order_by(
            "n_reviews",
            "slice",
            "display",
            dm.F("priority").desc(nulls_last=True),
            "id",
        )
