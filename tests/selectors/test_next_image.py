"""Tests for the generalized review-ordering selector (next_image + strategies)."""

import pytest

from django_dirt_ratings import ordering, services
from django_dirt_ratings.exceptions import ApplicationError
from django_dirt_ratings.models import DisplayMode, Image, Ratings, Session, Step
from django_dirt_ratings.selectors import next_image

BREADTH = ordering.BreadthFirst()
ANOMALY = ordering.AnomalyFirst()
SWEEP = ordering.AnomalySweep()
STEP = Step.FMAP_COREGISTRATION


@pytest.fixture
def make_image(db):
    """Create a reviewable Image with a fresh identity, overriding any field."""

    def _make(step=STEP, **overrides) -> Image:
        n = Image.objects.count()
        fields = {
            "img": f"images/f{n}-d0s{n}.avif",
            "digest": "d" * 16,
            "file1": f"f{n}.nii.gz",
            "display": DisplayMode.X,
            "step": step,
            "slice": n,
        }
        return Image.objects.create(**(fields | overrides))

    return _make


@pytest.fixture
def session(db) -> Session:
    return Session.objects.create(step=STEP)


# Every scan of a step renders the same (cut, orientation) positions.
POSITIONS = [
    (0, DisplayMode.X),
    (0, DisplayMode.Y),
    (1, DisplayMode.X),
    (1, DisplayMode.Y),
]


@pytest.fixture
def make_scan(make_image):
    """Create one scan's whole view set, keyed by position, sharing one priority."""

    def _make(
        file1: str, *, priority: float | None
    ) -> dict[tuple[int, DisplayMode], Image]:
        return {
            (cut, display): make_image(
                file1=file1, slice=cut, display=display, priority=priority
            )
            for cut, display in POSITIONS
        }

    return _make


@pytest.mark.django_db
class TestAnomalyFirst:
    def test_highest_priority_served_first(self, make_image):
        make_image(priority=0.5)
        high = make_image(priority=3.0)
        make_image(priority=1.0)

        served = next_image(step=STEP, strategy=ANOMALY)

        assert served.pk == high.pk

    def test_null_priority_sorts_after_scored(self, make_image):
        scored = make_image(priority=0.1)
        make_image(priority=None)

        served = next_image(step=STEP, strategy=ANOMALY)

        assert served.pk == scored.pk

    @pytest.mark.parametrize(
        "strategy", [ANOMALY, BREADTH, SWEEP], ids=["anomaly", "breadth", "sweep"]
    )
    def test_all_null_falls_back_to_insertion_order(self, make_image, strategy):
        first = make_image(priority=None)
        make_image(priority=None)

        served = next_image(step=STEP, strategy=strategy)

        assert served.pk == first.pk

    def test_exclude_last(self, make_image):
        high = make_image(priority=3.0)
        low = make_image(priority=1.0)

        served = next_image(step=STEP, strategy=ANOMALY, exclude=high.pk)

        assert served.pk == low.pk

    def test_empty_raises(self):
        with pytest.raises(ApplicationError):
            next_image(step=STEP, strategy=ANOMALY)

    def test_anti_starvation(self, make_image, session):
        """Reviewing the worst image advances the queue instead of ping-ponging.

        priority is static, so if it were the primary sort key the loop would keep
        serving the worst image forever. Because n_reviews is the backbone, reviewing
        the worst sinks it below the still-unreviewed images.
        """
        worst = make_image(priority=3.0)
        second = make_image(priority=2.0)
        make_image(priority=1.0)
        services.rating_create(image=worst, session=session, rating=Ratings.FAIL)

        # Nothing is excluded: the reviewed worst simply no longer dominates.
        served = next_image(step=STEP, strategy=ANOMALY)

        assert served.pk == second.pk


@pytest.mark.django_db
class TestAnomalySweep:
    """One view of every scan per pass; passes walk the (slice, display) positions."""

    @pytest.fixture
    def worst(self, make_scan):
        return make_scan("worst.nii.gz", priority=3.0)

    @pytest.fixture
    def other(self, make_scan):
        return make_scan("other.nii.gz", priority=1.0)

    def test_starts_with_the_first_view_of_the_worst_scan(self, other, worst):
        # `other` is created first (lower ids), so only priority puts `worst` ahead.
        served = next_image(step=STEP, strategy=SWEEP)

        assert served.pk == worst[(0, DisplayMode.X)].pk

    @pytest.mark.parametrize(
        "strategy, expected_file1",
        [
            pytest.param(SWEEP, "other.nii.gz", id="sweep-moves-on-to-the-next-scan"),
            pytest.param(ANOMALY, "worst.nii.gz", id="anomaly-first-stays-on-it"),
        ],
    )
    def test_a_second_view_of_a_scan_waits_for_every_other_scan(
        self, worst, other, session, strategy, expected_file1
    ):
        services.rating_create(
            image=worst[(0, DisplayMode.X)], session=session, rating=Ratings.FAIL
        )

        served = next_image(step=STEP, strategy=strategy)

        assert served.file1 == expected_file1

    def test_positions_are_swept_cut_major(self, worst, other, session):
        """After cut 0 of every scan in one orientation: cut 0 in the next orientation."""
        for scan in (worst, other):
            services.rating_create(
                image=scan[(0, DisplayMode.X)], session=session, rating=Ratings.PASS
            )

        served = next_image(step=STEP, strategy=SWEEP)

        assert served.pk == worst[(0, DisplayMode.Y)].pk

    def test_deepens_once_every_view_has_been_seen(self, worst, other, session):
        for image in (*worst.values(), *other.values()):
            services.rating_create(image=image, session=session, rating=Ratings.PASS)

        served = next_image(step=STEP, strategy=SWEEP)

        assert served.pk == worst[(0, DisplayMode.X)].pk

    def test_single_view_scans_degenerate_to_anomaly_first(self, make_image):
        make_image(step=Step.DTIFIT, slice=None, display=DisplayMode.Z, priority=1.0)
        high = make_image(
            step=Step.DTIFIT, slice=None, display=DisplayMode.Z, priority=2.0
        )

        served = next_image(step=Step.DTIFIT, strategy=SWEEP)

        assert served.pk == high.pk


def _query_plan(strategy: ordering.OrderingStrategy) -> str:
    """SQLite's plan for the exact seek next_image runs (step filter + exclude)."""
    qs = Image.objects.filter(step=STEP.value).exclude(id=0)
    return strategy.order(qs).values_list("id", flat=True).explain()


@pytest.mark.django_db
class TestCoveringIndex:
    """Every strategy is one covering index seek, as each index comment claims.

    A strategy whose order_by drifts from its index (or an index whose migration
    was forgotten) still serves the right image, just via a sort over the whole
    step; only the query plan tells the difference.
    """

    @pytest.mark.parametrize(
        "strategy, index",
        [
            pytest.param(BREADTH, "image_next", id="breadth"),
            pytest.param(ANOMALY, "image_priority", id="anomaly"),
            pytest.param(SWEEP, "image_sweep", id="sweep"),
        ],
    )
    def test_seeks_its_covering_index(self, strategy, index):
        assert f"USING COVERING INDEX {index}" in _query_plan(strategy)

    @pytest.mark.parametrize(
        "strategy", [BREADTH, ANOMALY, SWEEP], ids=["breadth", "anomaly", "sweep"]
    )
    def test_needs_no_sort(self, strategy):
        assert "TEMP B-TREE" not in _query_plan(strategy)
