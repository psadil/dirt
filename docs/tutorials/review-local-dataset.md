# Review a local dataset

This tutorial generalizes the [Quickstart](../getting-started/quickstart.md) to your own
preprocessed data sitting on a local disk (a laptop, a lab workstation, or a cluster
filesystem). The shape is always the same: **index → render → serve**.

## 1. Prepare the environment

```shell
pixi install -e manage
export DB=$PWD/db/dirt.db
export DJANGO_SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key as g; print(g())')"
pixi run -e manage manage migrate
pixi run -e manage manage createcachetable --database cache
```

Your derivatives directory should contain a `dataset_description.json` marking it as a
derivative dataset, e.g.:

```json
{
    "Name": "my-study-fmriprep",
    "BIDSVersion": "1.4.0",
    "DatasetType": "derivative",
    "GeneratedBy": [{"Name": "fmriprep"}]
}
```

## 2. Index the dataset

DIRT discovers derivatives through [bidslake](https://github.com/psadil/bidslake), which
builds a queryable DuckDB catalog of a dataset. Index once:

```shell
pixi run -e manage bidslake index -i /path/to/derivatives/fmriprep --adapter freesurfer --adapter fmriprep -o study.duckdb
```

(Both adapters, always: `freesurfer` for the note below, and `fmriprep` because the
coregistration specs discover transforms by their `from`/`to` entities, which only
that adapter surfaces.)

## 3. Render the QC images

One command renders every step whose files are present in the catalog:

```shell
pixi run -e manage manage render study.duckdb
```

Useful flags:

- `--step masks` (repeatable) renders only the named step(s); the default is every step
  found. Choices: `masks`, `spatial_normalization`, `surface_localization`,
  `fmap_coregistration`, `t1w_coregistration`, `dtifit`.
- `--update` re-renders images in place, preserving any ratings already collected.
- `--sub` / `--res` filter to a subject label or resolution.
- `--workers` sets the number of render worker processes (default: CPU count).

`render` finds each derivative and its related files by BIDS concept — the brain mask's
same-space T1w, the field map's `IntendedFor` bold targets and their boldref / mask /
transform — rather than by string-matching filenames, so it is robust to the exact naming
a pipeline emits. Derivatives it can't resolve are skipped with a log line.

For a real cluster example (SLURM + `apptainer run docker://psadil/dirt:manage`), see
[`tools/write_imgs`](https://github.com/psadil/dirt/blob/main/tools/write_imgs).

!!! note "FreeSurfer (surface localization)"
    FreeSurfer `recon-all` output is standardized but not BIDS, so index it with the
    FreeSurfer adapter — and, because a `sourcedata/freesurfer` nesting defeats the
    term-map anchor, as its own dataset:

    ```shell
    pixi run -e manage bidslake index -i /path/to/derivatives/freesurfer \
        --adapter freesurfer --adapter fmriprep --dataset-id freesurfer -o study.duckdb
    ```

    Add it to the same `study.duckdb` and `render` will pick up `surface_localization`.

    A shared catalog's physical shape is frozen by whichever index run creates it, and
    an adapter widens it — so **every** index run into `study.duckdb` must pass the
    union of adapters any of its datasets needs (here `--adapter freesurfer
    --adapter fmriprep`, matching the run in step 2), or bidslake refuses the
    mismatched run.

!!! note "DTI-fit"
    `dtifit` reads FSL-dtifit outputs (e.g. from qsirecon). Index that derivatives tree
    like any other; `render` discovers the FA map and its V1/V2/V3 eigenvectors by
    entity. This step is not yet exercised by the sample dataset.

## 4. Order by quality metrics (optional)

By default images are served **breadth-first** (fewest reviews first). To surface unusual
scans sooner, write a [review plan](../concepts/review-ordering.md) and apply it *before*
rendering. For example, to see the most atypically-sized brain masks first:

```toml
#:schema https://psadil.github.io/dirt/api/plan.schema.json
# dirt.toml
[ordering]
strategy = "anomaly_first"

[steps.masks]
order_by = "mask_volume"
direction = "two_sided"     # atypically large OR small is worth a look
subgroup = ["space"]
```

`mask_volume` needs no declaring: DIRT [measures every metric it can](../concepts/metrics.md)
for every file, and a plan only picks which one orders the queue.

```shell
pixi run -e manage manage plan dirt.toml     # validate + activate (before render)
pixi run -e manage manage render study.duckdb
pixi run -e manage manage prioritize         # z-score the measure into the ordering key
```

The metric only *reorders* — it never hides an image, and it is invisible to reviewers. Rerun
`manage prioritize` after adding data. See [Review ordering](../concepts/review-ordering.md)
for the strategies (`breadth_first`, `anomaly_first`, `anomaly_sweep`) and the full schema.

## 5. Serve and review

Point the web app at the same `db/` and `media/` directories:

```shell
docker run --rm -it -v $PWD/db:/app/db -v $PWD/media:/app/media \
  --env-file=.env -p 8000:8000 psadil/dirt
```

Create a login (reviews are per-account) and open <http://localhost:8000>:

```shell
pixi run -e manage manage create_rater alice
```

The reviewer never needs the neuro stack — the images are pre-rendered files under
`media/`, and the database carries their identities and the reviews. To serve from a
remote deployment instead, see [Deployment](../deployment.md) and `manage push`.
