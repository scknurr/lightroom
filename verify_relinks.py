"""Verify filename-based relink candidates using catalog captureTime + importHash size.

Read-only. Reads the audit snapshot and relink-candidates-unverified.csv, writes
relink-candidates-verified.csv (adds match_reason, verdict) and relink-summary.json
next to them. Does not touch the live catalog, originals, or sidecars.
"""
import argparse
import collections
import csv
import json
import sqlite3
import unicodedata
from pathlib import Path


def norm(s):
    return unicodedata.normalize('NFC', s).casefold()


def size_from_import_hash(h):
    if not h:
        return None
    parts = h.rsplit(':', 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('output', type=Path, help='Audit output folder (contains catalog-audit-snapshot.sqlite and the candidate CSVs).')
    args = ap.parse_args()
    out = args.output.resolve()
    snapshot = out / 'catalog-audit-snapshot.sqlite'
    if (out / (snapshot.name + '-wal')).exists():
        raise SystemExit('Unexpected snapshot WAL; refusing immutable read.')
    c = sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True)
    c.execute('PRAGMA query_only=ON')

    # Build (full_path_norm) -> (file_id, image_id, captureTime, size, folder_id)
    # And (file_id) -> same tuple, for cheap lookups by missing_file_id.
    print('Indexing catalog paths...', flush=True)
    by_path, by_fid = {}, {}
    q = '''SELECT af.id_local file_id, i.id_local image_id, i.captureTime, i.originalCaptureTime,
                  af.importHash, af.idx_filename, r.absolutePath root, f.pathFromRoot rel, f.id_local folder_id
           FROM AgLibraryFile af
           JOIN AgLibraryFolder f ON f.id_local = af.folder
           JOIN AgLibraryRootFolder r ON r.id_local = f.rootFolder
           LEFT JOIN Adobe_images i ON i.rootFile = af.id_local AND i.masterImage IS NULL'''
    for row in c.execute(q):
        file_id, image_id, cap, ocap, imp, name, root, rel, folder_id = row
        full = str(Path(root) / rel / name)
        entry = {
            'file_id': file_id,
            'image_id': image_id,
            'captureTime': cap or ocap or '',
            'size': size_from_import_hash(imp),
            'folder_id': folder_id,
            'path': full,
        }
        by_path[norm(full)] = entry
        by_fid[file_id] = entry

    cand_path = out / 'relink-candidates-unverified.csv'
    verified_path = out / 'relink-candidates-verified.csv'
    rows_in = list(csv.DictReader(cand_path.open()))

    verdict_counts = collections.Counter()
    reasons = collections.Counter()
    out_rows = []
    for r in rows_in:
        mid = int(r['missing_file_id'])
        missing = by_fid.get(mid)
        cand = by_path.get(norm(r['candidate_path']))
        verdict = 'unverifiable'
        reason = ''
        if not missing:
            reason = 'missing_file_id_not_in_catalog'
        elif cand is None:
            # Candidate is not currently cataloged (an orphan on disk).
            # Catalog alone cannot confirm; would need to read the file.
            verdict = 'needs_file_read'
            reason = 'candidate_not_cataloged'
        else:
            mcap, msize = missing['captureTime'], missing['size']
            ccap, csize = cand['captureTime'], cand['size']
            same_cap = bool(mcap) and bool(ccap) and mcap == ccap
            same_size = msize is not None and csize is not None and msize == csize
            if same_cap and same_size:
                verdict, reason = 'verified_same_photo', 'captureTime+size'
            elif same_cap and not same_size:
                verdict, reason = 'conflict', 'captureTime_match_but_size_differs'
            elif same_size and not same_cap:
                verdict, reason = 'weak_match', 'size_match_but_captureTime_differs'
            else:
                verdict, reason = 'different_photo', 'captureTime_and_size_differ'
        verdict_counts[verdict] += 1
        reasons[(verdict, reason)] += 1
        out_rows.append({
            **r,
            'verified_same_photo': verdict == 'verified_same_photo',
            'verdict': verdict,
            'match_reason': reason,
            'missing_captureTime': (missing or {}).get('captureTime', ''),
            'missing_size': (missing or {}).get('size') or '',
            'candidate_captureTime': (cand or {}).get('captureTime', ''),
            'candidate_size': (cand or {}).get('size') or '',
        })

    fields = list(rows_in[0].keys()) + [
        'verdict', 'match_reason',
        'missing_captureTime', 'missing_size',
        'candidate_captureTime', 'candidate_size',
    ]
    # verified_same_photo is already in the input fields; keep order
    seen = set()
    fields = [x for x in fields if not (x in seen or seen.add(x))]
    with verified_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(out_rows)

    # Build per-missing-file verdict: best candidate wins
    priority = {'verified_same_photo': 4, 'weak_match': 3, 'needs_file_read': 2,
                'conflict': 1, 'different_photo': 0, 'unverifiable': -1}
    best = {}
    for row in out_rows:
        mid = row['missing_file_id']
        if mid not in best or priority[row['verdict']] > priority[best[mid]['verdict']]:
            best[mid] = row
    per_missing_counts = collections.Counter(b['verdict'] for b in best.values())

    # Breakdown: how many missing files per bucket are verified-reclaimable
    def bucket(p):
        if p.startswith('/Volumes/Y DRIVE/Pictures/2019/2019-07-19'):
            return 'nested-2019'
        if p.startswith('/Users/Steve/Pictures/2020/'):
            return 'local-2020'
        if p.lower().startswith('/users/steve/downloads'):
            return 'downloads'
        if p.startswith('/Volumes/EOS_DIGITAL'):
            return 'camera-card'
        if p.startswith('/Users/Steve/Dropbox/'):
            return 'dropbox-2015'
        if p.startswith('/Volumes/Y DRIVE/'):
            return 'ydrive-other'
        if p.startswith('/Users/Steve/Desktop/'):
            return 'desktop'
        return 'other'

    bucket_verdict = collections.defaultdict(collections.Counter)
    missing_rows = list(csv.DictReader((out / 'missing-original-paths.csv').open()))
    verdict_by_mid = {b['missing_file_id']: b['verdict'] for b in best.values()}
    for mr in missing_rows:
        bk = bucket(mr['path'])
        v = verdict_by_mid.get(mr['file_id'], 'no_filename_candidate')
        bucket_verdict[bk][v] += 1

    # For verified matches, how many of those catalog-side candidates are ALREADY cataloged
    # at a different path? That's a cleanup indicator (missing entry is a stale duplicate).
    stale_dupes = 0
    reclaim_from_elsewhere = 0
    for b in best.values():
        if b['verdict'] != 'verified_same_photo':
            continue
        if b['candidate_already_cataloged'] in ('True', True):
            stale_dupes += 1
        else:
            reclaim_from_elsewhere += 1

    summary = {
        'inputs': {
            'relink_candidates_unverified_rows': len(rows_in),
            'distinct_missing_files_with_candidates': len(set(r['missing_file_id'] for r in rows_in)),
            'missing_files_total': len(missing_rows),
        },
        'per_candidate_row_verdicts': dict(verdict_counts),
        'per_missing_file_best_verdict': dict(per_missing_counts),
        'verified_matches': {
            'stale_duplicate_in_catalog': stale_dupes,
            'reclaim_from_orphan_on_disk': reclaim_from_elsewhere,
        },
        'bucket_breakdown': {k: dict(v) for k, v in bucket_verdict.items()},
        'notes': [
            'verified_same_photo = same filename + same captureTime + same size from importHash.',
            'conflict = captureTime match but size differs (likely re-edited/re-exported).',
            'weak_match = size match but captureTime differs (filename collision across cameras).',
            'needs_file_read = candidate exists on disk but is not in catalog; would need EXIF/file hash to confirm.',
            'different_photo = captureTime and size both differ; almost certainly a filename collision.',
            'stale_duplicate_in_catalog: a verified match whose candidate_path is already a separate catalog entry; the missing entry is likely a dead duplicate (no data loss).',
            'reclaim_from_orphan_on_disk: a verified match whose candidate_path is NOT currently cataloged; relinking it would restore the catalog entry.',
            'This script reads only the audit snapshot; it does not touch the live catalog, originals, or sidecars.',
        ],
    }
    (out / 'relink-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2), flush=True)
    c.close()


if __name__ == '__main__':
    main()
