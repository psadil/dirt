# DIRT

**DIRT** (the Derived Imaging Review Tool) is a web application for quality control
(QC) of neuroimaging derivatives at the scale of large consortia. It was built for the
Acute to Chronic Pain Signatures (A2CPS) project, which is collecting scans from more
than 2,800 participants.

## Why

Neuroimaging preprocessing pipelines fail on some fraction of scans. At consortium scale,
even a low failure rate means hundreds of affected scans, and automated failure detection
is not yet reliable enough to replace human inspection — but manual QC is hard to carry
out at that scale. DIRT exists to make thorough manual review fast enough to keep up.

## How it works

DIRT has three parts:

- **Image generation.** Given a preprocessed dataset in
  [BIDS](https://bids.neuroimaging.io/) layout, DIRT uses the BIDS metadata to locate
  derivatives and render a compact set of QC images for each one — for a brain mask, for
  example, five informative slices in each of three orientations, and three for a
  coregistration. Where a failure is best seen across a whole volume, the image is
  animated.
- **A review-ordering algorithm.** During a session, images are served one at a time
  using a *breadth-first* strategy: the next image comes from whichever scan has been
  reviewed the fewest times. Effort is spread across the whole dataset first, then deepens
  per scan as time allows. *anomaly-first* re-ranks within each review-depth band by how
  atypical a measure is; *anomaly-sweep* additionally serves one view of every scan per
  pass. See [Review ordering](concepts/review-ordering.md).
- **A review platform.** A mobile-friendly Django web app presents the images, collects
  ratings, and stores everything in a SQL database, so several reviewers can work on the
  same dataset at once.

## Two kinds of review

Each derivative is reviewed with whichever interaction best matches how it tends to fail —
either clicking to mark problem locations, or rating the whole image pass / unsure / fail.
See [Two kinds of review](concepts/interaction-types.md).

## Where to start

- New here? Follow the [Quickstart](getting-started/quickstart.md) to review a bundled
  sample dataset in a few minutes.
- Reviewing your own data? See [Review a local dataset](tutorials/review-local-dataset.md).
- Standing it up for a team? See [Deployment](deployment.md).

DIRT is in production on A2CPS Release 2.0 (~29 TB across ~2.2 million derivative files).
