"""Tests for review-plan parsing, validation, and persistence."""

import pytest

from django_dirt_ratings import models, plan, services

VALID = """
name = "demo"

[ordering]
strategy = "anomaly_first"

[steps.masks]
order_by = "mask_volume"
direction = "two_sided"
subgroup = ["space"]
"""


class TestParseValid:
    """VALID is parsed once per test; each test asserts one facet of the result."""

    @pytest.fixture
    def parsed(self) -> plan.Plan:
        return plan.parse(VALID)

    @pytest.fixture
    def mask_plan(self, parsed: plan.Plan) -> plan.StepPlan:
        step_plan = parsed.step_plan(models.Step.MASK)
        if step_plan is None:  # a setup guard, not the assertion under test
            pytest.fail("VALID declares a [steps.masks] table")
        return step_plan

    def test_name(self, parsed):
        assert parsed.name == "demo"

    def test_strategy(self, parsed):
        assert parsed.strategy == models.ReviewStrategy.ANOMALY_FIRST

    def test_reviewable_steps(self, parsed):
        assert parsed.reviewable_steps == (models.Step.MASK,)

    def test_step_order_by(self, mask_plan):
        assert mask_plan.order_by == "mask_volume"

    def test_step_direction(self, mask_plan):
        assert mask_plan.direction == models.MetricDirection.TWO_SIDED

    def test_step_subgroup(self, mask_plan):
        assert mask_plan.subgroup == ("space",)

    def test_a_computed_metric_needs_no_measure_block(self, mask_plan):
        assert mask_plan.measures == ()


class TestParseEmpty:
    """An empty plan is the default: breadth-first over every step."""

    @pytest.fixture
    def parsed(self) -> plan.Plan:
        return plan.parse("")

    def test_strategy_defaults_to_breadth_first(self, parsed):
        assert parsed.strategy == models.ReviewStrategy.BREADTH_FIRST

    def test_declares_no_steps(self, parsed):
        assert parsed.steps == ()


def test_the_sweep_strategy_parses():
    parsed = plan.parse('[ordering]\nstrategy = "anomaly_sweep"\n')

    assert parsed.strategy == models.ReviewStrategy.ANOMALY_SWEEP


class TestParseCatalogMeasure:
    CATALOG = (
        '[steps.dtifit]\norder_by="fd_mean"\ndirection="higher_worse"\n'
        '[[steps.dtifit.measures]]\nname="fd_mean"\ncatalog="fd_mean"\n'
    )

    @pytest.fixture
    def dtifit_plan(self) -> plan.StepPlan:
        step_plan = plan.parse(self.CATALOG).step_plan(models.Step.DTIFIT)
        if step_plan is None:  # a setup guard, not the assertion under test
            pytest.fail("CATALOG declares a [steps.dtifit] table")
        return step_plan

    def test_measure_is_read_from_the_catalog(self, dtifit_plan):
        assert dtifit_plan.catalog_measures == dtifit_plan.measures


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            '[steps.masks]\n[[steps.masks.measures]]\nname="x"\ncompute="mask_volume"',
            id="legacy-compute-measure",
        ),
        pytest.param(
            '[steps.masks]\n[[steps.masks.measures]]\nname="x"',
            id="measure-without-a-catalog-key",
        ),
        pytest.param('[steps.masks]\norder_by="nope"', id="order_by-is-not-a-measure"),
        pytest.param("[steps.bogus]\n", id="unknown-step"),
        pytest.param('[ordering]\nstrategy="wat"', id="unknown-strategy"),
        pytest.param('nam = "demo"', id="unknown-top-level-key"),
        pytest.param("[ordering]\ntriage_depth=1", id="stale-triage-depth"),
        pytest.param("[steps.masks]\nbogus=1", id="unknown-step-block-key"),
        pytest.param(
            '[steps.masks]\n[[steps.masks.measures]]\nname="x"\ncatalog="c"\nbogus=1',
            id="unknown-measure-key",
        ),
        pytest.param('[steps.masks]\nstep="masks"', id="step-set-in-block"),
        pytest.param("[steps.masks]\nmin_cv=-0.5", id="negative-min_cv"),
        pytest.param("[steps.masks]\nmin_cv=true", id="boolean-min_cv"),
        pytest.param('[steps.masks]\nmin_cv="0.1"', id="string-min_cv"),
        pytest.param("[steps.masks]\nmin_spread=true", id="boolean-min_spread"),
        pytest.param(
            '[steps.masks]\ndirection="sideways"\norder_by="mask_volume"',
            id="unknown-direction",
        ),
        pytest.param(
            '[steps.masks]\n[[steps.masks.measures]]\nname="x"\ncatalog="c"\n'
            'catalog_suffix="bold"',
            id="cross-dataset-measure-without-match-keys",
        ),
        pytest.param(
            '[steps.masks]\n[[steps.masks.measures]]\nname="x"\ncatalog="c"\n'
            'match=["sub"]',
            id="cross-dataset-measure-without-a-suffix",
        ),
    ],
)
def test_invalid_raises_plan_error(text):
    with pytest.raises(plan.PlanError):
        plan.parse(text)


class TestComputedMetricsNeedNoDeclaration:
    """Computed metrics are always measured, so a plan only ever names one."""

    @pytest.fixture
    def mask_plan(self) -> plan.StepPlan:
        step_plan = plan.parse('[steps.masks]\norder_by="fov_cutoff_max"').step_plan(
            models.Step.MASK
        )
        if step_plan is None:  # a setup guard, not the assertion under test
            pytest.fail("the parsed plan declares a [steps.masks] table")
        return step_plan

    def test_order_by_accepts_any_computed_metric(self, mask_plan):
        assert mask_plan.order_by == "fov_cutoff_max"

    def test_it_needs_no_measure_block(self, mask_plan):
        assert mask_plan.measures == ()

    def test_a_legacy_compute_block_says_what_to_do_instead(self):
        text = (
            '[steps.masks]\n[[steps.masks.measures]]\nname="v"\ncompute="mask_volume"'
        )

        with pytest.raises(plan.PlanError, match='order_by = "mask_volume"'):
            plan.parse(text)


@pytest.fixture
def applied(db) -> models.ReviewPlan:
    """VALID, applied — the arrange+act shared by the persistence tests."""
    return services.plan_apply(name="demo", text=VALID)


@pytest.mark.django_db
class TestApply:
    def test_record_is_active(self, applied):
        assert applied.is_active

    def test_record_is_the_active_record(self, applied):
        assert plan.active_record() == applied

    def test_active_plan_reflects_the_applied_text(self, applied):
        assert plan.active().strategy == models.ReviewStrategy.ANOMALY_FIRST


@pytest.mark.django_db
class TestReapplySameText:
    """Plans are deduped by content hash, so re-applying is a no-op."""

    @pytest.fixture
    def reapplied(self, applied) -> models.ReviewPlan:
        return services.plan_apply(name="demo", text=VALID)

    def test_reuses_the_same_record(self, applied, reapplied):
        assert reapplied.pk == applied.pk

    def test_stores_no_duplicate(self, reapplied):
        assert models.ReviewPlan.objects.count() == 1


@pytest.mark.django_db
class TestApplyDifferentText:
    BREADTH = VALID.replace('strategy = "anomaly_first"', 'strategy = "breadth_first"')

    @pytest.fixture
    def superseding(self, applied) -> models.ReviewPlan:
        return services.plan_apply(name="b", text=self.BREADTH)

    def test_new_record_is_active(self, superseding):
        assert superseding.is_active

    def test_previous_record_is_deactivated(self, applied, superseding):
        applied.refresh_from_db()

        assert not applied.is_active

    def test_active_plan_is_the_new_one(self, superseding):
        assert plan.active().strategy == models.ReviewStrategy.BREADTH_FIRST


@pytest.mark.django_db
class TestNoPlanApplied:
    def test_there_is_no_active_record(self):
        assert plan.active_record() is None

    def test_active_plan_is_the_default(self):
        assert plan.active().strategy == models.ReviewStrategy.BREADTH_FIRST


@pytest.mark.django_db
class TestSessionPinsServingFacet:
    """A Session copies the plan's serving facet at start-up (see plan module doc)."""

    @pytest.fixture
    def planned_session(self, applied) -> models.Session:
        return services.session_create(step=models.Step.MASK)

    @pytest.fixture
    def unplanned_session(self, db) -> models.Session:
        return services.session_create(step=models.Step.MASK)

    def test_strategy_is_copied_from_the_plan(self, planned_session):
        assert planned_session.strategy == models.ReviewStrategy.ANOMALY_FIRST

    def test_strategy_defaults_without_a_plan(self, unplanned_session):
        assert unplanned_session.strategy == models.ReviewStrategy.BREADTH_FIRST


class TestJsonSchema:
    """The generated schema mirrors exactly what ``parse`` accepts."""

    @pytest.fixture(scope="class")
    def schema(self) -> dict:
        return plan.Plan.model_json_schema()

    def test_strategy_enum_matches_the_review_strategies(self, schema):
        assert schema["$defs"]["ReviewStrategy"]["enum"] == [
            s.value for s in models.ReviewStrategy
        ]

    def test_unknown_top_level_keys_are_forbidden(self, schema):
        assert schema["additionalProperties"] is False

    def test_step_names_match_the_steps(self, schema):
        assert set(schema["$defs"]["StepsTable"]["properties"]) == {
            s.cli_name for s in models.Step
        }

    def test_injected_step_field_is_hidden(self, schema):
        assert "step" not in schema["$defs"]["StepPlan"]["properties"]
