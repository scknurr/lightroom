"""Walk every attached drive looking for (a) copies of the 2,242 missing Lightroom
originals, and (b) a per-drive, per-year photo count.

Read-only. Does not touch the catalog or any originals.
Outputs:
  2026-10-05/drive-hits-missing.csv — every match (filename + size) for a missing file
  2026-10-05/drive-photo-counts.csv — media file counts per drive/year
  2026-10-05/drive-scan-summary.json — rollup
"""
import collections
import csv
import json
import os
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path


OUT = Path(__file__).resolve().parent / '2026-10-05'
SNAPSHOT = OUT / 'catalog-audit-snapshot.sqlite'

MEDIA = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.psd', '.psb', '.dng', '.cr2',
         '.cr3', '.nef', '.arw', '.raf', '.orf', '.rw2', '.heic', '.heif', '.avif',
         '.mov', '.mp4', '.m4v', '.avi', '.mts', '.m2ts', '.3gp', '.webp', '.gif'}

DRIVES = ['G DRIVE', 'H DRIVE', 'L Drive', 'M DRIVE', 'P DRIVE', 'W Drive', 'Y DRIVE']

SKIP_DIR_NAMES = {'.Spotlight-V100', '.fseventsd', '.Trashes', '.DocumentRevisions-V100',
                  '.TemporaryItems', '.DS_Store', '.fcpcache'}


def norm(s):
    return unicodedata.normalize('NFC', s).casefold()


def main():
    # Build missing-filename → list of (file_id, missing_path, expected_size) from catalog
    c = sqlite3.connect(SNAPSHOT.as_uri() + '?mode=ro&immutable=1', uri=True)
    missing_rows = list(csv.DictReader((OUT / 'missing-original-paths.csv').open()))
    missing_ids = {int(r['file_id']) for r in missing_rows}
    expected = collections.defaultdict(list)  # norm(basename) -> list of (file_id, path, size)
    for fid, name, imp in c.execute(
        'SELECT id_local, idx_filename, importHash FROM AgLibraryFile'):
        if fid not in missing_ids:
            continue
        size = None
        if imp and ':' in imp:
            try: size = int(imp.rsplit(':', 1)[1])
            except ValueError: pass
        # Match against the missing path too to recover original path
        pass
    # Map file_id -> missing path from CSV, and lookup size separately
    path_by_fid = {int(r['file_id']): r['path'] for r in missing_rows}
    size_by_fid = {}
    for fid, imp in c.execute('SELECT id_local, importHash FROM AgLibraryFile'):
        if fid in missing_ids and imp and ':' in imp:
            try: size_by_fid[fid] = int(imp.rsplit(':', 1)[1])
            except ValueError: pass
    for fid, path in path_by_fid.items():
        base = norm(Path(path).name)
        expected[base].append((fid, path, size_by_fid.get(fid)))
    c.close()
    print(f'Missing filenames to find: {len(expected):,} (across {len(missing_rows):,} missing files)', flush=True)

    hits = []  # dicts to write
    photo_counts = collections.defaultdict(collections.Counter)  # drive -> year -> count
    started = time.monotonic()
    drive_scanned = {}

    for drv in DRIVES:
        root = Path('/Volumes') / drv
        if not root.exists():
            print(f'{drv}: not mounted, skip', flush=True)
            continue
        print(f'=== scanning {drv} ===', flush=True)
        d_start = time.monotonic()
        n_files = 0
        n_matched_names = 0
        n_media = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # prune hidden/system
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in SKIP_DIR_NAMES]
            for name in filenames:
                n_files += 1
                ext = os.path.splitext(name)[1].lower()
                if ext in MEDIA:
                    n_media += 1
                    # year bucket: find a /YYYY/ segment in path
                    parts = dirpath.split(os.sep)
                    year = next((p for p in parts if len(p) == 4 and p.isdigit() and 1900 < int(p) < 2100), 'unknown')
                    photo_counts[drv][year] += 1
                nkey = norm(name)
                if nkey in expected:
                    n_matched_names += 1
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                        sz = st.st_size
                        mtime = st.st_mtime
                    except OSError as e:
                        sz = None; mtime = None
                    for fid, missing_path, exp_size in expected[nkey]:
                        hits.append({
                            'drive': drv,
                            'file_id': fid,
                            'missing_path': missing_path,
                            'hit_path': full,
                            'hit_size': sz if sz is not None else '',
                            'expected_size': exp_size if exp_size is not None else '',
                            'size_match': (sz is not None and exp_size is not None and sz == exp_size),
                            'hit_mtime': int(mtime) if mtime else '',
                        })
            if (time.monotonic() - d_start) % 20 < 0.1 and n_files > 0:
                pass
        d_elapsed = time.monotonic() - d_start
        drive_scanned[drv] = {
            'files_total': n_files,
            'media_files': n_media,
            'filename_hits': n_matched_names,
            'elapsed_seconds': round(d_elapsed, 1),
        }
        print(f'  {drv}: {n_files:,} files · {n_media:,} media · {n_matched_names:,} filename matches · {d_elapsed:.0f}s', flush=True)

    with (OUT / 'drive-hits-missing.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, ['drive', 'file_id', 'missing_path', 'hit_path',
                               'hit_size', 'expected_size', 'size_match', 'hit_mtime'])
        w.writeheader(); w.writerows(hits)

    with (OUT / 'drive-photo-counts.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['drive', 'year', 'photo_count'])
        for drv, c in photo_counts.items():
            for year, n in sorted(c.items()):
                w.writerow([drv, year, n])

    # Summary: per file_id, best drive hit
    best_hit = {}
    for h in hits:
        key = h['file_id']
        prev = best_hit.get(key)
        if not prev or (h['size_match'] and not prev['size_match']):
            best_hit[key] = h

    reclaim_by_drive_size = collections.Counter(h['drive'] for h in best_hit.values() if h['size_match'])
    reclaim_by_drive_namesonly = collections.Counter(h['drive'] for h in best_hit.values() if not h['size_match'])
    reclaimable_missing = len({h['file_id'] for h in best_hit.values() if h['size_match']})

    summary = {
        'missing_total': len(missing_rows),
        'missing_with_any_drive_hit': len(best_hit),
        'missing_with_size_verified_hit': reclaimable_missing,
        'per_drive_scan': drive_scanned,
        'size_verified_hits_by_drive': dict(reclaim_by_drive_size),
        'name_only_hits_by_drive': dict(reclaim_by_drive_namesonly),
        'per_drive_media_year_counts': {drv: dict(c) for drv, c in photo_counts.items()},
        'total_elapsed_seconds': round(time.monotonic() - started, 1),
        'notes': [
            'Size match uses importHash-encoded file size from the catalog.',
            'Name-only hits are a filename collision risk; verify EXIF before relinking.',
            'Scan is read-only; no files or catalog state were modified.',
        ],
    }
    (OUT / 'drive-scan-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
