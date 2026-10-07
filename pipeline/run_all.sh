#!/bin/zsh
# Mechanical pipeline for every year, most valuable (most picks/ratings) first. Resumable.
cd "$(dirname "$0")"
LOG="/Volumes/G DRIVE/LR-RESCUE.noindex/logs"; mkdir -p "$LOG"
YEARS=("$@")
(( ${#YEARS} )) || YEARS=(2019 2020 2024 2025 2021 2023 2022 2026 2015 2018 2017 2016 2013 2009 2014 2012 2010 2011 2008)
for y in $YEARS; do
  echo "$(date +%H:%M) START $y"
  THUMB_WORKERS=8 ./run_year.sh $y > "$LOG/$y.log" 2>&1 && echo "$(date +%H:%M) DONE $y" || echo "$(date +%H:%M) FAILED $y (see $LOG/$y.log)"
done
echo ALL_DONE
