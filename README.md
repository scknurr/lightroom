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

Outputs go into a dated folder (e.g. `2026-10-05/`). That folder is gitignored because it holds the catalog snapshot and personal file paths.

## Setup

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
brew install exiftool
```

## Findings so far (2026-10-05 audit)

- 246,017 images in the catalog. Only 150 have keywords.
- 2,242 originals show as missing. 2,082 of them are verified copies on other drives:
  - `~/Pictures/2020` → `H DRIVE/Pictures/2020`
  - a broken nested 2019 import, whose files already exist on Y, L and P
- The attached drives hold about 1.6M media files. The catalog indexes about 15% of them.

## Roadmap

1. Thumbnail cache built from Lightroom's preview cache, plus the JPEGs embedded in RAW files
2. Technical cull: blur, exposure, best-of-burst
3. CLIP embeddings → aesthetic score, zero-shot tags, theme clustering, learning from existing picks and ratings
4. Editor pass on top candidates: hero selection, captions, crop and treatment suggestions, licensability flags
5. Write-back through a Lightroom plugin: keywords, collections, ratings, develop settings
6. Outputs: web gallery, stock and licensing sets, Instagram queue
