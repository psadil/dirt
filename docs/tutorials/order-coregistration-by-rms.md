# Order coregistrations by RMS displacement

This walkthrough puts the [review plan](../concepts/review-ordering.md) to work on a concrete question: of all the field-map coregistrations in a study, which ones should a reviewer look at first? We answer it by measuring how far each coregistration affine actually moves the brain — its RMS displacement in millimeters — and serving the worst alignments first.

It assumes you can already index and render a dataset (see
[Review a local dataset](review-local-dataset.md)). The only new piece is a small
`dirt.toml`.

## What we are ordering, and by what

The `fmap_coregistration` step renders, for each BOLD run, the reference EPI against the coregistered boldref, masked and animated so a reviewer can flip between them and judge the alignment by eye. fMRIPrep writes that alignment as a boldref→field-map affine (`*_xfm.txt`). We want the runs whose affine is most out of place to rise to the top of the queue.

A natural answer to "how much does this affine move the brain?" is the RMS displacement ([Jenkinson et al. 2002](https://doi.org/10.1016/S1053-8119(02)91132-8)), which folds rotation, translation, and scale/shear into a single number. A large displacement flags a suspect alignment (assuming the participant did not move much between scans). The brain's centroid and radius come from the run's own brain mask, so the measure needs no template.

## Write the plan

Save this as `dirt.toml` next to your catalog:

```toml
#:schema https://psadil.github.io/dirt/api/plan.schema.json
name = "study coregistration QC"

[ordering]
strategy = "anomaly_first"        # fewest-reviews first, worst-aligned first within a depth band

[steps.fmap_coregistration]
order_by = "affine_displacement"  # RMS mm the coregistration affine moves the brain
direction = "higher_worse"        # one-sided: a bigger displacement is more suspect
```

`affine_displacement` is [measured for every coregistration DIRT renders](../concepts/metrics.md)
whether or not a plan mentions it; naming it here is what makes it the ordering key.

Two choices are worth calling out, because they differ from the [`mask_volume` example](review-local-dataset.md#4-order-by-quality-metrics-optional):

- `direction = "higher_worse"`. A coregistration is only ever wrong by moving too much; there is no such thing as a suspiciously small displacement.
- No `subgroup`. Displacement is already a physical distance in millimeters, comparable across runs regardless of voxel size or template space, so there is no like-with-like grouping to do. (Volume needed `subgroup = ["space"]` because an MNI-space mask volume is not comparable to a native-space one.)

## Index, plan, render, prioritize

Activate the plan before you render, so each image is stamped with the plan it was rendered under (the measures themselves are computed either way):

```shell
pixi run -e manage bidslake index -i /path/to/derivatives/fmriprep --adapter freesurfer --adapter fmriprep -o study.duckdb
# parse and activate the plan, storing it in the database so it travels with the ratings
pixi run -e manage manage plan dirt.toml # validate → persist → activate

# render just the coregistration images and measure each one — `affine_displacement` from the run's brain mask and `*_xfm.txt`, alongside every other metric those inputs support.
pixi run -e manage manage render study.duckdb --step fmap_coregistration

# turn those raw measures into the ordering key, converting each value to a robust z-score (how unusual this run is relative to the rest) and write it to `Image.priority`. Rerun it after new data lands. It is a safe, idempotent recompute.
pixi run -e manage manage prioritize # measures → Image.priority
```

You can drop `--step fmap_coregistration` to render every step; the plan only orders the steps it names, and everything else stays breadth-first.

The same block works verbatim for `[steps.t1w_coregistration]`, which checks each run against the session's anatomical rather than against a field map. `affine_displacement` is measured there too, from the `from-boldref_to-T1w` affine — so a study acquired without field maps can still be ordered this way.

## Serve and review

```shell
docker run --rm -it -v $PWD/db:/app/db --env-file=.env -p 8000:8000 psadil/dirt
```

Open <http://localhost:8000>. On the first pass (that is, when every image unreviewed) `anomaly_first` serves the whole set of coregistrations worst-alignment first, then naturally deepens to give everything a look. Each run renders nine views, and `anomaly_first` serves them back to back; to see one view of every run before a second view of any, set `strategy = "anomaly_sweep"`. The reviewer sees only the images; the millimeter metric is invisible to them and cannot anchor their judgment. Researchers can see the raw value and the derived priority in the admin.

## Where to go next

- Add a cross-dataset measure; order the same coregistrations by an MRIQC IQM (e.g. `fd_mean`) that lives in a sibling MRIQC dataset built from the same source. See the `catalog` / `catalog_suffix` / `match` example in [Review ordering](../concepts/review-ordering.md#the-review-plan-dirttoml).
