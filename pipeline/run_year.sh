#!/bin/zsh
# Usage: run_year.sh 2019 [2020 ...] — full pipeline for the given capture years (resumable).
set -e
cd "$(dirname "$0")"
PY=../.venv/bin/python
YEARS=("$@")
yargs=(); for y in $YEARS; do yargs+=(--year $y); done
$PY -u thumbs.py $yargs --workers ${THUMB_WORKERS:-8}
$PY -u technical.py --workers 14
$PY -W ignore -u embed.py --batch 48
$PY -W ignore -u privacy.py
$PY -u taste.py
$PY -u select_best.py $yargs
for y in $YEARS; do $PY -u dedupe.py --year $y; done
for y in $YEARS; do $PY -u review.py --year $y --out "/Volumes/G DRIVE/LR-RESCUE.noindex/review_$y.html"; done
echo PIPELINE_DONE
