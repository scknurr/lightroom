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

## Roadmap

1. ~~Thumbnail cache built from Lightroom's preview cache, plus the JPEGs embedded in RAW files~~ (`build_thumbs.py`)
2. Technical cull: blur, exposure, best-of-burst
3. CLIP embeddings → aesthetic score, zero-shot tags, theme clustering, learning from existing picks and ratings
4. Editor pass on top candidates: hero selection, captions, crop and treatment suggestions, licensability flags
5. Write-back through a Lightroom plugin: keywords, collections, ratings, develop settings
6. Outputs: web gallery, stock and licensing sets, Instagram queue
