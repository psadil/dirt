"""Tests for django_dirt_ratings views."""

import json
from typing import cast

import pytest
from django import forms
from django.urls import reverse

from django_dirt_ratings import services
from django_dirt_ratings.forms import IndexForm
from django_dirt_ratings.models import (
    Annotation,
    AnnotationCell,
    DisplayMode,
    Image,
    Rating,
    Ratings,
    Session,
    Step,
)
from django_dirt_ratings.views import (
    DTIFIT_VIEW,
    FMAP_COREGISTRATION_VIEW,
    MASK_VIEW,
    SPATIAL_NORMALIZATION_VIEW,
    SURFACE_LOCALIZATION_VIEW,
    T1W_COREGISTRATION_VIEW,
)

STEP_VIEWS = [
    (Step.MASK, MASK_VIEW),
    (Step.SPATIAL_NORMALIZATION, SPATIAL_NORMALIZATION_VIEW),
    (Step.SURFACE_LOCALIZATION, SURFACE_LOCALIZATION_VIEW),
    (Step.FMAP_COREGISTRATION, FMAP_COREGISTRATION_VIEW),
    (Step.DTIFIT, DTIFIT_VIEW),
    (Step.T1W_COREGISTRATION, T1W_COREGISTRATION_VIEW),
]


@pytest.fixture
def set_session(client):
    """Seed the browser session the views read, as a prior GET flow would."""

    def _set(**values) -> None:
        session = client.session
        session.update(values)
        session.save()

    return _set


@pytest.mark.django_db
class TestLayoutViewGet:
    @pytest.fixture
    def response(self, client):
        return client.get(reverse("index"))

    def test_responds_ok(self, response):
        assert response.status_code == 200

    def test_renders_the_index_template(self, response):
        assert "index.html" in [t.name for t in response.templates]


@pytest.mark.django_db
class TestLayoutViewPost:
    @pytest.mark.parametrize(
        "step, expected_view", STEP_VIEWS, ids=[v for _, v in STEP_VIEWS]
    )
    def test_step_redirects_to_its_view(self, client, step, expected_view):
        response = client.post(reverse("index"), data={"step": step})

        assert response.url == reverse(expected_view)

    def test_redirects(self, client):
        response = client.post(reverse("index"), data={"step": Step.MASK})

        assert response.status_code == 302

    def test_creates_a_session(self, client):
        client.post(reverse("index"), data={"step": Step.MASK})

        assert Session.objects.count() == 1

    def test_invalid_step_rerenders_the_form(self, client):
        response = client.post(reverse("index"), data={"step": 999})

        assert response.status_code == 200


@pytest.mark.django_db
class TestRateViewGet:
    @pytest.mark.parametrize(
        "view_name, template",
        [(FMAP_COREGISTRATION_VIEW, "rate.html"), (MASK_VIEW, "click.html")],
    )
    def test_step_renders_its_template(self, client, view_name, template):
        response = client.get(reverse(view_name))

        assert template in [t.name for t in response.templates]

    @pytest.mark.parametrize("view_name", [FMAP_COREGISTRATION_VIEW, MASK_VIEW])
    def test_responds_ok(self, client, view_name):
        response = client.get(reverse(view_name))

        assert response.status_code == 200

    def test_rating_renders_without_a_blank_choice(self, client):
        # A blank '---------' radio would render pre-checked, defeating the
        # native required-group validation that blocks ratingless submits.
        response = client.get(reverse(FMAP_COREGISTRATION_VIEW))

        assert "---------" not in response.text


@pytest.mark.django_db
class TestRateViewPost:
    @pytest.fixture
    def response(self, client, set_session, fmap_image, fmap_session):
        set_session(
            step=Step.FMAP_COREGISTRATION,
            image_id=fmap_image.pk,
            session_id=fmap_session.pk,
        )
        return client.post(
            reverse(FMAP_COREGISTRATION_VIEW), data={"rating": Ratings.PASS}
        )

    def test_answers_with_the_next_partial(self, response):
        # The submission renders the next image itself; redirecting to the
        # partial cost a second round trip for a response it could already build.
        assert response.status_code == 200

    def test_creates_one_rating(self, response):
        assert Rating.objects.count() == 1

    def test_records_the_verdict(self, response):
        assert Rating.objects.get().rating == Ratings.PASS

    def test_links_the_rated_image(self, response, fmap_image):
        assert Rating.objects.get().image_id == fmap_image.pk

    def test_invalid_rating_rerenders_the_form(self, client):
        # No 'rating' key at all — the form is required to reject it.
        response = client.post(reverse(FMAP_COREGISTRATION_VIEW), data={})

        assert response.status_code == 200

    def test_invalid_rating_creates_nothing(self, client):
        client.post(reverse(FMAP_COREGISTRATION_VIEW), data={})

        assert Rating.objects.count() == 0


@pytest.mark.django_db
class TestClickViewPost:
    @pytest.fixture
    def post_cells(self, client, set_session, mask_image, mask_session):
        """Submit a click review of `mask_image` with the given cells."""

        def _post(cells):
            set_session(
                step=Step.MASK, image_id=mask_image.pk, session_id=mask_session.pk
            )
            return client.post(
                reverse(MASK_VIEW),
                data={
                    "grid_cols": 28,
                    "grid_rows": 21,
                    "cells": json.dumps(cells),
                },
            )

        return _post

    @pytest.fixture
    def marked(self, post_cells):
        return post_cells([[3, 5, Ratings.FAIL], [3, 6, Ratings.UNSURE]])

    @pytest.fixture
    def unmarked(self, post_cells):
        return post_cells([])

    def test_answers_with_the_next_partial(self, marked):
        assert marked.status_code == 200

    def test_creates_one_annotation(self, marked):
        assert Annotation.objects.count() == 1

    def test_links_the_reviewed_image(self, marked, mask_image):
        assert Annotation.objects.get().image_id == mask_image.pk

    def test_records_the_grid(self, marked):
        annotation = Annotation.objects.get()

        assert (annotation.grid_cols, annotation.grid_rows) == (28, 21)

    def test_stores_every_marked_cell(self, marked):
        assert AnnotationCell.objects.count() == 2

    def test_empty_payload_is_accepted(self, unmarked):
        assert unmarked.status_code == 200

    def test_empty_payload_still_creates_an_annotation(self, unmarked):
        assert Annotation.objects.count() == 1

    def test_empty_payload_stores_no_cells(self, unmarked):
        assert AnnotationCell.objects.count() == 0


@pytest.mark.django_db
class TestRatePartial:
    def test_responds_ok(self, client, set_session, mask_image, mask_session):
        set_session(step=Step.MASK, session_id=mask_session.pk)

        response = client.get(reverse("rate_partial"))

        assert response.status_code == 200

    def test_tracks_the_served_image(
        self, client, set_session, mask_image, mask_session
    ):
        """The served image_id is recorded for the next transition."""
        set_session(step=Step.MASK, session_id=mask_session.pk)

        client.get(reverse("rate_partial"))

        assert client.session["image_id"] == mask_image.pk

    def test_no_session_step_is_404(self, client):
        response = client.get(reverse("rate_partial"))

        assert response.status_code == 404


@pytest.mark.django_db
class TestNextImagePrefetch:
    """The partial names the image the next request will serve, so the browser
    can have the bytes cached before the swap that needs them."""

    @pytest.fixture
    def following(self, make_image):
        return make_image(slice=1, file1="next.nii.gz")

    def test_the_upcoming_image_is_offered(
        self, client, set_session, mask_image, following, mask_session
    ):
        set_session(step=Step.MASK, session_id=mask_session.pk)

        response = client.get(reverse("rate_partial"))

        assert response.context["next_image_url"] == following.img.url

    def test_the_last_image_offers_nothing(
        self, client, set_session, mask_image, mask_session
    ):
        set_session(step=Step.MASK, session_id=mask_session.pk)

        response = client.get(reverse("rate_partial"))

        assert response.context["next_image_url"] is None

    def test_the_prediction_is_what_the_next_request_serves(
        self, client, set_session, mask_image, following, mask_session
    ):
        """A wrong guess would fetch bytes nobody then uses."""
        set_session(step=Step.MASK, session_id=mask_session.pk)
        predicted = client.get(reverse("rate_partial")).context["next_image_url"]

        served = client.get(reverse("rate_partial")).context["image_url"]

        assert served == predicted

    def test_the_prediction_is_a_hint_not_a_reservation(
        self, client, set_session, mask_image, following, make_image, mask_session
    ):
        """Nothing is held for the browser between the two requests: the image
        served is whatever the ordering says *now*. Storing the prediction and
        handing it back would pin a reviewer to a stale choice the moment a
        second reviewer rates that image first."""
        third = make_image(slice=2, file1="third.nii.gz")
        set_session(step=Step.MASK, session_id=mask_session.pk)
        # Serves `mask_image` and predicts `following`; also pins the image_id
        # the next request excludes.
        client.get(reverse("rate_partial"))

        # A second reviewer gets there first, sinking the predicted image.
        services.rating_create(
            image=following, session=mask_session, rating=Ratings.PASS
        )

        served = client.get(reverse("rate_partial")).context["image_url"]

        # `third`, not the predicted `following` — recomputed, not replayed.
        assert served == third.img.url

    def test_the_submission_serves_what_the_partial_prefetched(
        self, client, set_session, fmap_image, make_image, fmap_session
    ):
        """The whole point: the bytes the browser was told to warm are the bytes
        the very next response asks it to display."""
        make_image(slice=1, file1="next.nii.gz", step=Step.FMAP_COREGISTRATION)
        set_session(step=Step.FMAP_COREGISTRATION, session_id=fmap_session.pk)
        prefetched = client.get(reverse("rate_partial")).context["next_image_url"]

        response = client.post(
            reverse(FMAP_COREGISTRATION_VIEW), data={"rating": Ratings.PASS}
        )

        assert response.context["image_url"] == prefetched

    def test_the_submission_answers_with_the_next_image(
        self, client, set_session, fmap_image, make_image, fmap_session
    ):
        upcoming = make_image(
            slice=1, file1="next.nii.gz", step=Step.FMAP_COREGISTRATION
        )
        set_session(
            step=Step.FMAP_COREGISTRATION,
            image_id=fmap_image.pk,
            session_id=fmap_session.pk,
        )

        response = client.post(
            reverse(FMAP_COREGISTRATION_VIEW), data={"rating": Ratings.PASS}
        )

        assert response.context["image_url"] == upcoming.img.url

    def test_anomaly_strategy_serves_highest_priority(
        self, client, set_session, fmap_session
    ):
        """The cookie-pinned strategy threads through to next_image."""
        _fmap_image(file1="lo.nii.gz", slice=0, priority=0.2)
        worst = _fmap_image(file1="hi.nii.gz", slice=1, priority=5.0)
        set_session(
            step=Step.FMAP_COREGISTRATION,
            session_id=fmap_session.pk,
            strategy="anomaly_first",
        )

        client.get(reverse("rate_partial"))

        assert client.session["image_id"] == worst.pk

    def test_sweep_strategy_serves_the_first_view_of_the_worst_scan(
        self, client, set_session, fmap_session
    ):
        """The cookie-pinned sweep threads through: position outranks insertion order."""
        _fmap_image(file1="hi.nii.gz", slice=1, priority=5.0)
        first_view = _fmap_image(file1="hi.nii.gz", slice=0, priority=5.0)
        set_session(
            step=Step.FMAP_COREGISTRATION,
            session_id=fmap_session.pk,
            strategy="anomaly_sweep",
        )

        client.get(reverse("rate_partial"))

        assert client.session["image_id"] == first_view.pk


@pytest.mark.django_db
class TestClickPartialReference:
    """The landmark reference beside a spatial-normalization image."""

    @pytest.fixture
    def normalization_image(self, make_image) -> Image:
        return make_image(
            file1="sub-01_space-MNI152NLin2009cAsym_desc-preproc_T1w.nii.gz",
            display=DisplayMode.Z,
            step=Step.SPATIAL_NORMALIZATION,
            slice=1,
        )

    @pytest.fixture
    def served(self, client, set_session, normalization_image, db):
        """The partial, for an image whose measured space is known."""

        def _serve(entities: dict | None):
            if entities is not None:
                services.measured_file_upsert(
                    step=int(Step.SPATIAL_NORMALIZATION),
                    file1=normalization_image.file1,
                    entities=entities,
                )
            session = Session.objects.create(step=Step.SPATIAL_NORMALIZATION)
            set_session(step=Step.SPATIAL_NORMALIZATION, session_id=session.pk)
            return client.get(reverse("click_partial"))

        return _serve

    def test_a_measured_space_resolves_its_figure(self, served):
        response = served({"space": "MNI152NLin2009cAsym", "cohort": None})

        assert response.context["reference_url"].endswith(
            "ratings/reference/tpl-MNI152NLin2009cAsym/z-1.avif"
        )

    def test_an_unmeasured_image_gets_no_reference(self, served):
        response = served(None)

        assert "reference_url" not in response.context

    def test_a_space_without_figures_gets_no_reference(self, served):
        response = served({"space": "MNI152NLin6Sym", "cohort": None})

        assert "reference_url" not in response.context

    def test_finishing_the_step_clears_the_reference(
        self, client, set_session, normalization_image, db
    ):
        """Nothing is under review any more, so there is nothing to reference —
        the click step's completion template is the one that clears the panel."""
        session = Session.objects.create(step=Step.SPATIAL_NORMALIZATION)
        set_session(
            step=Step.SPATIAL_NORMALIZATION,
            session_id=session.pk,
            image_id=normalization_image.pk,
        )

        response = client.get(reverse("click_partial"))

        assert "click_complete.html" in [t.name for t in response.templates]

    def test_a_step_without_landmarks_gets_no_reference(
        self, client, set_session, mask_image, mask_session
    ):
        set_session(step=Step.MASK, session_id=mask_session.pk)

        response = client.get(reverse("click_partial"))

        assert "reference_url" not in response.context


def _fmap_image(*, priority: float, **overrides) -> Image:
    image = services.image_create(
        img=b"\x89PNG",
        display=DisplayMode.X,
        step=Step.FMAP_COREGISTRATION,
        **overrides,
    )
    Image.objects.filter(pk=image.pk).update(priority=priority)
    return image


def _offered_steps(form: IndexForm) -> set[str]:
    field = form.fields["step"]
    if not isinstance(field, forms.ChoiceField):  # a setup guard, not an assertion
        pytest.fail("IndexForm.step must be a ChoiceField")
    # Django normalizes assigned choices, so reading back gives (value, label) pairs.
    choices = cast("list[tuple[object, str]]", field.choices)
    return {str(c[0]) for c in choices if c[0] not in ("", None)}


@pytest.mark.django_db
class TestIndexFormGating:
    def test_offers_only_planned_steps(self):
        services.plan_apply(name="t", text="[steps.masks]\n")

        offered = _offered_steps(IndexForm())

        assert offered == {str(Step.MASK.value)}

    def test_offers_all_steps_when_no_plan(self):
        offered = _offered_steps(IndexForm())

        assert offered == {str(s.value) for s in Step}
