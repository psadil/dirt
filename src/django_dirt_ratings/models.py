from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    # Stub-only (django-stubs): the type of a reverse FK accessor. Declaring the
    # accessors below as plain annotations gives them types without the mypy
    # plugin; Django ignores un-assigned annotations, so no field is created.
    from django.db.models.fields.related_descriptors import RelatedManager

DEFAULT_GRID_COLS = 28
"""Default number of columns in the annotation grid (see ``Step.grid_cols``)."""


class Step(models.IntegerChoices):
    MASK = 0
    SPATIAL_NORMALIZATION = 1
    SURFACE_LOCALIZATION = 2
    FMAP_COREGISTRATION = 3
    DTIFIT = 4
    T1W_COREGISTRATION = 5, "T1w Coregistration"

    @property
    def image_type(self) -> str:
        """MIME subtype of the figures stored for this step.

        Every step stores AVIF: a single frame for the static views
        (mask, spatial normalization, surface localization) and an animation for
        the multi-frame views (both coregistrations, dtifit).
        """
        return "avif"

    @property
    def related_name(self) -> str:
        """Related query name from Image to this step's submission model."""
        match self:
            case Step.MASK | Step.SPATIAL_NORMALIZATION | Step.SURFACE_LOCALIZATION:
                return "annotation"
            case _:
                return "rating"

    @property
    def grid_cols(self) -> int:
        """Columns in the annotation grid overlaid on this step's images.

        Rows are derived on the client from the image aspect ratio and
        recorded per-submission on ``Annotation``.
        """
        return DEFAULT_GRID_COLS

    @property
    def cli_name(self) -> str:
        """The step's CLI/plan token (matches its ``StepSpec`` name).

        Canonical, web-safe name↔Step mapping so the review plan (``plan.py``)
        and forms can resolve step tokens without importing the ingest registry
        (which pulls in the neuro stack).
        """
        match self:
            case Step.MASK:
                return "masks"
            case Step.SPATIAL_NORMALIZATION:
                return "spatial_normalization"
            case Step.SURFACE_LOCALIZATION:
                return "surface_localization"
            case Step.FMAP_COREGISTRATION:
                return "fmap_coregistration"
            case Step.DTIFIT:
                return "dtifit"
            case Step.T1W_COREGISTRATION:
                return "t1w_coregistration"
        raise AssertionError(f"unhandled step {self!r}")

    @classmethod
    def from_cli_name(cls, name: str) -> "Step":
        """The Step for a CLI/plan token; raises ``ValueError`` if unknown."""
        for step in cls:
            if step.cli_name == name:
                return step
        raise ValueError(f"unknown step {name!r}")


class Ratings(models.IntegerChoices):
    PASS = 0
    UNSURE = 1
    FAIL = 2


class DisplayMode(models.IntegerChoices):
    X = 0
    Y = 1
    Z = 2


class ReviewStrategy(models.TextChoices):
    """How the next image to review is chosen.

    ``TextChoices`` (str-valued, with ``.choices``/``.values``) so the same enum
    serves both the review-plan TOML validation and the ``Session.strategy`` field.
    """

    # fewest reviews first (the default)
    BREADTH_FIRST = "breadth_first"
    # breadth backbone, most-atypical re-ranked within a review-depth band
    ANOMALY_FIRST = "anomaly_first"
    # anomaly_first, but one view of every scan per pass (see ordering.AnomalySweep)
    ANOMALY_SWEEP = "anomaly_sweep"


class MetricDirection(models.TextChoices):
    """Which values of a measure are "atypical" (drive ``Image.priority``)."""

    TWO_SIDED = "two_sided"  # atypical either way is suspect (e.g. mask volume)
    HIGHER_WORSE = "higher_worse"  # larger is more suspect (e.g. motion fd_mean)
    LOWER_WORSE = "lower_worse"  # smaller is more suspect (e.g. snr, cnr)


class ComputedMetric(models.TextChoices):
    """The metrics DIRT computes itself, by their canonical `Metric.name`.

    Web-safe home for the names (like :class:`Step`), so the review plan can
    validate an ``order_by`` against them — and publish them in its JSON Schema —
    without importing the ingest stack's numpy/nibabel. The extractors that
    produce them live in ``management.ingest.measures``, which guards at import
    time that its registry emits exactly these members and no others.
    """

    # Geometry of the brain mask alone.
    MASK_VOLUME = "mask_volume"
    FOV_CUTOFF_DORSAL = "fov_cutoff_dorsal"
    FOV_CUTOFF_VENTRAL = "fov_cutoff_ventral"
    FOV_CUTOFF_MAX = "fov_cutoff_max"
    # How far a coregistration affine moves the brain.
    AFFINE_DISPLACEMENT = "affine_displacement"
    # Which tissue the frame cuts through (a proxy; see the metrics concept page).
    FOV_CUTOFF_CORTEX = "fov_cutoff_cortex"
    FOV_CUTOFF_CEREBELLUM = "fov_cutoff_cerebellum"
    FOV_CUTOFF_BRAINSTEM = "fov_cutoff_brainstem"
    FOV_CUTOFF_CEREBRAL_WM = "fov_cutoff_cerebral_wm"
    # What fraction of each structure a narrower FOV missed outright (exact).
    FOV_EXCLUDED_CORTEX = "fov_excluded_cortex"
    FOV_EXCLUDED_CEREBELLUM = "fov_excluded_cerebellum"
    FOV_EXCLUDED_BRAINSTEM = "fov_excluded_brainstem"
    FOV_EXCLUDED_CEREBRAL_WM = "fov_excluded_cerebral_wm"


class BaseModel(models.Model):
    id: int  # the auto pk (django-stubs declares only `pk` without the plugin)
    created_at = models.DateTimeField(db_index=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ReviewPlan(BaseModel):
    """A persisted ``dirt.toml`` review plan — the QC provenance for a dataset.

    Stored verbatim (``toml``) with a ``content_hash`` so re-applying an identical
    plan is idempotent. ``render`` stamps each :class:`Image` it produces with the
    active plan (the "pipeline facet": what was measured); a rating :class:`Session`
    copies the plan's serving facet (strategy) onto itself. Exactly one
    plan is ``is_active`` at a time.
    """

    name = models.TextField(default="", blank=True)
    content_hash = models.TextField(unique=True)
    toml = models.TextField()
    is_active = models.BooleanField(default=False)


class Session(BaseModel):
    step = models.IntegerField(choices=Step.choices)
    user = models.TextField(default=None, null=True, blank=True)
    # Serving facet of the active ReviewPlan, pinned at session start so editing the
    # plan mid-review never disturbs an in-flight session.
    strategy = models.TextField(
        choices=ReviewStrategy.choices, default=ReviewStrategy.BREADTH_FIRST
    )


class Image(BaseModel):
    # The rendered bytes live as ordinary media (see storage.py for the
    # layout); the name is assigned from storage.image_name, never generated
    # by the field itself, so re-renders replace rather than collision-rename.
    img = models.FileField(max_length=255)
    digest = models.CharField(
        max_length=16,
        help_text=(
            "16-hex sha256 prefix of the stored image bytes. Names the media "
            "file (cache busting: new bytes, new URL) and lets the push client "
            "compare local and remote content without reading either file."
        ),
    )
    slice = models.IntegerField(null=True, blank=True)
    file1 = models.TextField(max_length=512)
    file2 = models.TextField(max_length=512, null=True, blank=True)
    display = models.IntegerField(choices=DisplayMode.choices)
    step = models.IntegerField(choices=Step.choices)
    n_reviews = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Denormalized count of review submissions (Rating/Annotation rows) for "
            "this image, maintained by the services layer. Lets the review-ordering "
            "selector find the least-reviewed image with an index seek instead of an "
            "aggregate scan over every image of a step."
        ),
    )
    priority = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Advisory ordering key for the anomaly_first and anomaly_sweep "
            "strategies, computed by `manage prioritize` as a robust "
            "within-subgroup modified z-score "
            "(MAD-based; |z| for two-sided measures). Larger = more atypical, "
            "surfaced earlier; 0.0 = typical (including a subgroup with no "
            "meaningful spread); NULL = no measure (sorts after scored images, i.e. "
            "breadth-first fallback). Only ever reorders — it never filters or hides "
            "an image."
        ),
    )
    review_plan = models.ForeignKey(
        "ReviewPlan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="The plan under which this image was rendered.",
    )
    review_plan_id: int | None

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["slice", "file1", "display", "step"], name="image_meta"
            ),
        )
        indexes = (
            # Serves the breadth_first strategy: filter by step, order by
            # (n_reviews, id) — an index range seek returning one leaf entry.
            models.Index(fields=["step", "n_reviews", "id"], name="image_next"),
            # Serves anomaly_first: order by (n_reviews, priority desc nulls
            # last, id). -priority matches F("priority").desc(nulls_last=True) so the
            # whole order is a covering forward scan — a single leaf seek, no sort.
            models.Index(
                fields=["step", "n_reviews", "-priority", "id"], name="image_priority"
            ),
            # Serves anomaly_sweep: order by (n_reviews, slice, display, priority
            # desc nulls last, id). A view's position sits between the review-depth
            # band and priority, so one seek walks a single (slice, display)
            # position across every scan before moving to the next. The column
            # order must match ordering.AnomalySweep.order token for token, or
            # SQLite sorts (a temp b-tree) instead of seeking.
            models.Index(
                fields=["step", "n_reviews", "slice", "display", "-priority", "id"],
                name="image_sweep",
            ),
        )


class MeasuredFile(BaseModel):
    """One measured NIfTI — the statistical unit the metrics belong to.

    An `Image` is one *view* of a file (a slice along an axis), so a file has nine
    to fifteen of them; a measurement belongs to the file, not the view. Keeping
    measurements here rather than on `Image` stores each value once and makes it a
    real column `manage prioritize` can group and aggregate, instead of a JSON blob
    repeated once per view.

    `entities` holds the categorical context a metric is compared *within* — the
    rational-subgroup entities harvested at discovery (`sub`, `ses`, `space`,
    `res`), plus any non-numeric catalog value. Numbers go to `Metric`.
    """

    step = models.IntegerField(choices=Step.choices)
    file1 = models.TextField(max_length=512)
    entities = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Categorical context for this file, e.g. {'space': 'MNI152NLin2009cAsym', "
            "'res': '2'}. `manage prioritize` scores a metric within a subgroup of these."
        ),
    )
    review_plan = models.ForeignKey(
        "ReviewPlan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="The plan active when this file was measured.",
    )
    review_plan_id: int | None
    metrics: "RelatedManager[Metric]"

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["step", "file1"], name="measured_file"),
        )


class Metric(BaseModel):
    """One measured value for one file.

    A row per (file, name) rather than a column per metric: DIRT gains metrics
    faster than it would want migrations, and every metric is the same shape — a
    number, or NULL for "we tried and could not measure it" (an empty mask, a
    segmentation on the wrong grid). NULL is deliberately not absence: absence
    means the extractor never applied to this file at all.
    """

    file = models.ForeignKey(
        MeasuredFile, on_delete=models.CASCADE, related_name="metrics"
    )
    file_id: int
    name = models.TextField(
        help_text=(
            "The metric's canonical name: a `ComputedMetric` value for a metric DIRT "
            "computes, or a plan-declared `catalog` measure name."
        )
    )
    value = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["file", "name"], name="metric_name"),
        )
        indexes = (
            # Serves `prioritize`, which reads one metric across a whole step.
            models.Index(fields=["name", "file"], name="metric_by_name"),
        )


class FromRequest(BaseModel):
    """Abstract base for models submitted during a rating session."""

    class Meta:
        abstract = True

    image = models.ForeignKey(Image, on_delete=models.CASCADE)
    image_id: int
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    session_id: int
    source_data_issue = models.BooleanField(
        default=False,
        verbose_name="I suspect there might be a problem with the image quality",
    )
    comments = models.TextField(
        default="",
        help_text="Please only add additional comments if necessary.",
        blank=True,
    )


class Annotation(FromRequest):
    """One annotation submission: the grid used and the cells marked on it.

    A submission with no marked cells (zero related ``AnnotationCell`` rows)
    records an image where the rater found nothing to mark but may have left
    flags or comments.
    """

    grid_cols = models.IntegerField()
    grid_rows = models.IntegerField()
    cells: "RelatedManager[AnnotationCell]"


class AnnotationCell(models.Model):
    """A single grid cell a rater marked as a problem.

    ``rating`` reuses the whole-image ``Ratings`` vocabulary: a marked cell is
    UNSURE (rater not confident) or FAIL (confident problem); PASS is implied
    by a cell being left unmarked.
    """

    annotation = models.ForeignKey(
        Annotation, on_delete=models.CASCADE, related_name="cells"
    )
    col = models.IntegerField()
    row = models.IntegerField()
    rating = models.IntegerField(choices=Ratings.choices)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["annotation", "col", "row"], name="annotation_cell"
            ),
        )


class Rating(FromRequest):
    rating = models.IntegerField(choices=Ratings.choices, verbose_name="")
