# lightroom-rescue

Tools for auditing and rescuing a large Lightroom Classic catalog (~246k images spread across several drives). The goal is to find the best shots in the backlog, then tag and develop them.

All scripts are **read-only**. None of them touch the live catalog, original photos or sidecars. Analysis runs against a private SQLite snapshot of the catalog.

## Scripts

| Script | What it does |
|---|---|
| `audit_catalog.py <catalog.lrcat> <out_dir>` | Takes a consistent snapshot of the catalog. Checks every cataloged original on disk and writes reports: missing files, extra media, keyword stats and duplicate candidates. |
| `verify_relinks.py <out_dir>` | Checks filename-based relink candidates against the catalog's capture time and file size (the size comes from `importHash`). |
| `scan_all_drives.py` | Walks every attached drive under `/Volumes`. Finds size-verified copies of the missing originals and counts media per drive and year. |
| `build_dashboard.py` | Builds `<out_dir>/dashboard.html`, a self-contained page summarizing everything above. |
| `build_thumbs.py <out_dir> --previews <Previews.lrdata>` | Builds an upright 512px thumbnail for every master image in `<out_dir>/thumbs/`, with a manifest in `thumbs.sqlite`. Uses Lightroom's preview cache first (both the `.lrprev` and the newer per-size layouts), then the JPEG embedded in RAW files, then the original itself. Resumable. |
| `cull.py <out_dir>` | Technical cull on those thumbnails. Scores blur (content-independent, so dark or low-contrast scenes aren't penalized) and exposure, groups bursts and near-duplicates by folder, camera, capture time and visual hash, and picks the best frame of each group. Your Lightroom picks and ratings always win. Writes `cull.sqlite`, `cull-summary.json` and a `cull-review.html` page for checking the thresholds. Resumable. |

Outputs go into a dated folder (e.g. `2026-10-05/`). That folder is gitignored because it holds the catalog snapshot and personal file paths.

## Setup

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
brew install exiftool
```

## Building thumbnails

```sh
.venv/bin/python build_thumbs.py 2026-10-05 \
    --previews "/Volumes/<drive>/<catalog> Previews.lrdata"
```

Try `--limit 500` first for a quick check. Ctrl-C is safe: rerun the same command and it picks up where it stopped. Failed images are skipped on later runs unless you pass `--retry-failed` (for example, after plugging in a drive). `exiftool` is used as a fallback for RAW layouts the built-in parser doesn't handle.

## Technical cull

```sh
.venv/bin/python cull.py 2026-10-05
```

Every master image gets one verdict:

- `keep`: the best frame of its group, or a single shot with no problems.
- `alt`: a similar frame that lost to the keeper.
- `soft`: blurry.
- `exposure`: very dark, washed out or blown.
- `unscored`: no thumbnail.

Verdicts go in the `cull` table with a 0–100 `tech_score` that later steps use. Nothing is deleted or written back to Lightroom.

Open `cull-review.html` to check the cutoffs. It shows the softest rejects, the softest frames that were kept, exposure problems and sample bursts. Adjust `--blur`, `--burst-gap`, `--scene-gap` and the other thresholds in `--help`, then rerun. Measurements are cached, so a rerun takes seconds.

Sharpness is judged on the 512px thumbnail, so this catches visible blur and camera shake, not focus that is slightly off at 100%.

## Roadmap

1. ~~Thumbnail cache built from Lightroom's preview cache, plus the JPEGs embedded in RAW files~~ (`build_thumbs.py`)
2. ~~Technical cull: blur, exposure, best-of-burst~~ (`cull.py`)
3. CLIP embeddings → aesthetic score, zero-shot tags, theme clustering, learning from existing picks and ratings
4. Editor pass on top candidates: hero selection, captions, crop and treatment suggestions, licensability flags
5. Write-back through a Lightroom plugin: keywords, collections, ratings, develop settings
6. Outputs: web gallery, stock and licensing sets, Instagram queue
