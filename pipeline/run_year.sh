#!/bin/zsh
# Usage: run_year.sh 2019 [2020 ...] — full pipeline for the given capture years (resumable).
set -e
cd "$(dirname "$0")"
PY=../.venv/bin/python
YEARS=("$@")
yargs=(); for y in $YEARS; do yargs+=(--year $y); done
$PY -u thumbs.py $yargs --workers 14
$PY -u technical.py --workers 14
$PY -W ignore -u embed.py --batch 48
$PY -u taste.py
$PY -u select_best.py $yargs
for y in $YEARS; do $PY -u review.py --year $y --out "/Volumes/G DRIVE/LR-RESCUE/review_$y.html"; done
echo PIPELINE_DONE
