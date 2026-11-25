"""Technical cull: sharpness, exposure and best-of-burst (roadmap step 2).

Read-only with respect to Lightroom: reads the audit snapshot and the thumbnail
cache from build_thumbs.py; writes only cull.sqlite, cull-summary.json and
cull-review.html next to thumbs.sqlite.

Per image (measured on the 512px thumbnail, so it catches gross blur and bad
exposure, not a slightly missed focus that only shows at 100%):
  blur          0 (crisp) .. 1 (mush): how little the edges change when the image
                is blurred again (Crete et al. 2007), averaged over the 3 most
                detailed tiles of a 4x4 grid so a sharp subject on a soft
                background still counts as sharp. Independent of scene contrast.
  sharp_global  Laplacian variance of the whole frame (for reference)
  mean/p01/p99  luminance level and spread; clip_hi/clip_lo = share of pixels
                blown (>=250) or crushed (<=5); contrast = luminance std dev
  dhash         64-bit difference hash, used to tell bursts from new scenes

Bursts and near-duplicates: images in the same folder from the same camera are
sorted by capture time and chained when they are within --burst-gap seconds
and look alike (dhash), or within --scene-gap seconds and look very alike.
Each group keeps its best frame(s); Lightroom picks and star ratings always
win over the score, and Lightroom rejects always lose.

Verdicts (one per master image):
  keep      best of its group, or a single shot without problems
  alt       a similar frame from the same burst/scene that lost to the keeper
  soft      blurry or motion-blurred
  exposure  very dark, washed out, or heavily clipped
  unscored  no thumbnail (missing original, video, ...)

Resumable: metrics are cached in cull.sqlite and only recomputed for new or
rebuilt thumbnails. Grouping and verdicts are recomputed each run (seconds),
so threshold flags can be tuned freely.

Usage:
  .venv/bin/python cull.py 2026-10-05
"""
import argparse
import bisect
import calendar
import collections
import html
import json
import multiprocessing as mp
import os
import random
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

METRIC_COLS = ['blur', 'sharp_global', 'mean', 'p01', 'p99', 'clip_hi', 'clip_lo', 'contrast', 'dhash', 'w', 'h']
GRID = 4
TOP_TILES = 3
MIN_EDGE = 320  # below this a thumbnail is too small to judge sharpness


# ---------------------------------------------------------------- metrics

def laplacian(g):
    return g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]


def box(a, axis, k=9):
    """k-tap moving average along one axis (edge-padded, same shape)."""
    pad = [(k // 2, k // 2) if ax == axis else (0, 0) for ax in range(2)]
    c = np.cumsum(np.pad(a, pad, mode='edge'), axis=axis, dtype=np.float64)
    c = np.concatenate([np.zeros_like(c.take([0], axis=axis)), c], axis=axis)
    n = c.shape[axis]
    return (c.take(range(k, n), axis=axis) - c.take(range(0, n - k), axis=axis)) / k


def blur_score(g):
    """No-reference blur (Crete et al. 2007) on the most detailed tiles.

    Re-blurring a sharp image removes a lot of edge contrast; re-blurring a
    blurry one barely changes it. Per tile and direction:
    (sum|dF| - sum max(0, |dF| - |dB|)) / sum|dF|, worst direction wins (catches
    motion blur). Calibrated on 512px thumbnails: crisp photos 0.15-0.30,
    visibly soft or shaken frames 0.45+.
    """
    maps = []
    for axis in (0, 1):
        dF = np.abs(np.diff(g, axis=axis))
        dB = np.abs(np.diff(box(g, axis), axis=axis))
        maps.append((dF, np.maximum(0, dF - dB)))
    h, w = g.shape
    tiles = []
    for r in range(GRID):
        for c in range(GRID):
            sl = (slice(r * h // GRID, (r + 1) * h // GRID), slice(c * w // GRID, (c + 1) * w // GRID))
            per_axis = [(dF[sl].sum(), V[sl].sum()) for dF, V in maps]
            energy = sum(f for f, _ in per_axis)
            b = max((f - v) / f if f > 0 else 1.0 for f, v in per_axis)
            tiles.append((energy, b))
    tiles.sort(reverse=True)
    top = tiles[:TOP_TILES]
    return float(sum(b for _, b in top) / len(top))


def dhash(im):
    """64-bit difference hash (horizontal gradients of a 9x8 grayscale)."""
    a = np.asarray(im.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v - (1 << 64) if v >= 1 << 63 else v  # fit SQLite's signed INTEGER


def measure(path):
    with Image.open(path) as im:
        g8 = im.convert('L')
    g = np.asarray(g8, dtype=np.float32)
    h, w = g.shape
    p01, p99 = np.percentile(g, [1, 99])
    n = g.size
    return {
        'blur': blur_score(g),
        'sharp_global': float(laplacian(g).var()),
        'mean': float(g.mean()),
        'p01': float(p01),
        'p99': float(p99),
        'clip_hi': float((g >= 250).sum()) / n,
        'clip_lo': float((g <= 5).sum()) / n,
        'contrast': float(g.std()),
        'dhash': dhash(g8),
        'w': w,
        'h': h,
    }


def work(job):
    iid, path, updated = job
    try:
        m = measure(path)
        return (iid, updated, *[m[k] for k in METRIC_COLS], '')
    except Exception as ex:
        return (iid, updated, *[None] * len(METRIC_COLS), f'{type(ex).__name__}: {ex}'[:300])


def hamming(a, b):
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count('1')


# ---------------------------------------------------------------- catalog

TIME_RE = re.compile(r'(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d)(?:\.(\d+))?)?')


def parse_time(s):
    """Lightroom captureTime -> seconds (naive, only used for gaps). Date-only -> None."""
    m = TIME_RE.match(s or '')
    if not m:
        return None
    y, mo, d, hh, mi, ss, frac = m.groups()
    try:
        t = calendar.timegm((int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0)))
    except (ValueError, OverflowError):
        return None
    return t + (float('0.' + frac) if frac else 0.0)


def load_catalog(out):
    snap = out / 'catalog-audit-snapshot.sqlite'
    if Path(str(snap) + '-wal').exists():
        raise SystemExit('Unexpected snapshot WAL; refusing immutable read.')
    c = sqlite3.connect(snap.as_uri() + '?mode=ro&immutable=1', uri=True)
    c.execute('PRAGMA query_only=ON')
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    exif = 'AgHarvestedExifMetadata' in tables
    q = f'''SELECT i.id_local, i.captureTime, i.pick, i.rating, af.folder, af.idx_filename,
                   {'x.cameraModelRef, x.cameraSNRef' if exif else 'NULL, NULL'}
            FROM Adobe_images i JOIN AgLibraryFile af ON af.id_local = i.rootFile
            {'LEFT JOIN AgHarvestedExifMetadata x ON x.image = i.id_local' if exif else ''}
            WHERE i.masterImage IS NULL'''
    imgs = {}
    for iid, ct, pick, rating, folder, fname, cam, sn in c.execute(q):
        imgs[iid] = {'t': parse_time(ct), 'capture': ct, 'pick': pick or 0, 'rating': rating or 0,
                     'folder': folder, 'name': fname, 'camera': (cam, sn)}
    c.close()
    return imgs


# ---------------------------------------------------------------- scoring

def flags_for(m, a):
    f = []
    if m['w'] is None:
        return f
    if max(m['w'], m['h']) >= MIN_EDGE and m['blur'] > a.blur:
        f.append('blurry')
    if m['mean'] < a.dark and m['p99'] < 128:
        f.append('dark')
    if m['mean'] > a.bright and m['p01'] > 140:
        f.append('washed_out')
    if m['clip_hi'] > a.clip:
        f.append('blown_highlights')
    if m['clip_lo'] > a.crush:
        f.append('crushed_shadows')
    if m['contrast'] < a.flat:
        f.append('low_contrast')
    return f


SEVERE = {'dark', 'washed_out', 'blown_highlights'}


def quality(m):
    """Within-group ranking score: sharpness first, exposure damage second."""
    if m.get('blur') is None:
        return -1e9
    return (-m['blur']
            - 2.0 * max(0.0, m['clip_hi'] - 0.01)
            - 1.0 * max(0.0, m['clip_lo'] - 0.05))


def user_rank(img):
    """Lightroom's own judgement: picks and stars beat any score; rejects lose."""
    if img['pick'] < 0:
        return -1
    return (2 if img['pick'] > 0 else 0) + (1 if img['rating'] > 0 else 0) + img['rating'] / 10


def build_groups(imgs, metrics, a):
    """Chain consecutive frames per (folder, camera) into burst/scene groups."""
    by_key = collections.defaultdict(list)
    for iid, img in imgs.items():
        if img['t'] is not None:
            by_key[(img['folder'], img['camera'])].append((img['t'], img['name'] or '', iid))
    groups = []
    for seq in by_key.values():
        seq.sort()
        cur = [seq[0][2]]
        for (t0, _, i0), (t1, _, i1) in zip(seq, seq[1:]):
            dt = t1 - t0
            h0, h1 = metrics.get(i0, {}).get('dhash'), metrics.get(i1, {}).get('dhash')
            ham = hamming(h0, h1) if h0 is not None and h1 is not None else None
            if ham is None:
                link = dt <= a.burst_gap
            else:
                link = (dt <= a.burst_gap and ham <= a.burst_ham) or (dt <= a.scene_gap and ham <= a.scene_ham)
            if link:
                cur.append(i1)
            else:
                if len(cur) > 1:
                    groups.append(cur)
                cur = [i1]
        if len(cur) > 1:
            groups.append(cur)
    return groups


def score_library(imgs, metrics, a):
    # Library-wide sharpness percentile, for a 0-100 technical score used by later steps
    blur_vals = sorted(m['blur'] for m in metrics.values() if m.get('blur') is not None)

    def sharper_than(v):
        return (len(blur_vals) - bisect.bisect_right(blur_vals, v)) / len(blur_vals) if blur_vals else 0.0

    res = {}
    for iid, img in imgs.items():
        m = metrics.get(iid)
        if not m or m.get('blur') is None:
            res[iid] = {'verdict': 'unscored', 'flags': [], 'tech': None, 'group': None, 'gsize': 1, 'rank': None}
            continue
        fl = flags_for(m, a)
        tech = 100 * sharper_than(m['blur']) - 25 * sum(f in SEVERE for f in fl) - 10 * sum(f in ('crushed_shadows', 'low_contrast') for f in fl)
        if 'blurry' in fl:
            tech = min(tech, 10)
        res[iid] = {'verdict': None, 'flags': fl, 'tech': round(max(0.0, min(100.0, tech)), 1),
                    'group': None, 'gsize': 1, 'rank': None}

    groups = build_groups(imgs, metrics, a)
    for gid, members in enumerate(groups, 1):
        order = sorted(members, key=lambda i: (user_rank(imgs[i]), quality(metrics.get(i, {}))), reverse=True)
        keep_n = max(a.keep, sum(user_rank(imgs[i]) > 0 for i in members))
        for rank, iid in enumerate(order, 1):
            r = res[iid]
            r.update(group=gid, gsize=len(members), rank=rank)
            if r['verdict'] == 'unscored':
                continue
            if rank > keep_n:
                r['verdict'] = 'alt'

    for iid, r in res.items():
        if r['verdict']:
            continue
        user = user_rank(imgs[iid]) > 0
        if 'blurry' in r['flags'] and not user:
            r['verdict'] = 'soft'
        elif SEVERE & set(r['flags']) and not user:
            r['verdict'] = 'exposure'
        else:
            r['verdict'] = 'keep'
    return res, groups


# ---------------------------------------------------------------- review page

def review_html(imgs, metrics, res, groups, summary, rel):
    def thumb(iid, cls=''):
        m = metrics.get(iid, {})
        r = res[iid]
        tip = (f"#{iid} {imgs[iid]['name']}  {imgs[iid]['capture'] or ''}\n"
               f"blur {m.get('blur', 0):.2f}  mean {m.get('mean', 0):.0f}  "
               f"blown {100 * (m.get('clip_hi') or 0):.1f}%  crushed {100 * (m.get('clip_lo') or 0):.1f}%\n"
               f"{r['verdict']}  tech {r['tech']}  {' '.join(r['flags'])}")
        return (f'<figure class="{cls}"><img loading="lazy" src="{html.escape(rel(iid))}" '
                f'title="{html.escape(tip)}"><figcaption>{html.escape(r["verdict"])}'
                f'{" · " + html.escape(", ".join(r["flags"])) if r["flags"] else ""}</figcaption></figure>')

    def grid(ids, cls=''):
        return '<div class="grid">' + ''.join(thumb(i, cls) for i in ids) + '</div>'

    rnd = random.Random(7)
    scored = [i for i, r in res.items() if r['verdict'] != 'unscored']
    by = collections.defaultdict(list)
    for i in scored:
        by[res[i]['verdict']].append(i)
    softest = sorted(by['soft'], key=lambda i: -metrics[i]['blur'])
    border = sorted((i for i in scored if res[i]['verdict'] == 'keep'), key=lambda i: -metrics[i]['blur'])
    big = sorted(groups, key=len, reverse=True)
    sample_groups = big[:6] + rnd.sample(big[6:], min(10, max(0, len(big) - 6)))

    sections = []
    vc = summary['verdicts']
    sections.append('<table><tr>' + ''.join(f'<th>{k}</th>' for k in vc) + '</tr><tr>'
                    + ''.join(f'<td>{v:,}</td>' for v in vc.values()) + '</tr></table>')
    sections.append(f'<h2>Softest frames <span>(highest blur score; cutoff is --blur {summary["thresholds"]["blur"]})</span></h2>'
                    + grid(softest[:48]))
    sections.append('<h2>Softest frames that were kept <span>(just under the cutoff; if these look blurry, lower --blur)</span></h2>'
                    + grid(border[:48]))
    sections.append('<h2>Exposure problems</h2>' + grid(rnd.sample(by['exposure'], min(48, len(by['exposure'])))))
    gh = []
    for g in sample_groups:
        ids = sorted(g, key=lambda i: res[i]['rank'] or 1e9)
        gh.append(f'<h3>{len(g)} frames · {html.escape(imgs[ids[0]]["capture"] or "")}</h3>'
                  + '<div class="grid">' + ''.join(thumb(i, 'win' if res[i]['verdict'] == 'keep' else '') for i in ids[:24]) + '</div>')
    sections.append('<h2>Bursts and near-duplicates <span>(the outlined frame is the keeper)</span></h2>' + ''.join(gh))
    sections.append('<h2>Random keepers</h2>' + grid(rnd.sample(by['keep'], min(48, len(by['keep'])))))

    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cull Review</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--acc:#16a34a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--acc:#4ade80}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1400px;padding:16px}}
h1{{margin:0 0 4px}} h2{{margin:32px 0 8px}} h2 span,.sub{{color:var(--mut);font-weight:400;font-size:13px}}
h3{{margin:16px 0 6px;font-size:13px;color:var(--mut)}}
table{{border-collapse:collapse}} th,td{{border:1px solid var(--line);padding:4px 10px;text-align:right}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
figure{{margin:0}} img{{width:100%;aspect-ratio:1;object-fit:contain;background:#0001;border-radius:4px;display:block}}
figure.win img{{outline:3px solid var(--acc);outline-offset:-3px}}
figcaption{{font-size:11px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
</style></head><body><h1>Technical cull</h1>
<p class="sub">{summary["scored"]:,} scored of {summary["master_images"]:,} master images · {summary["groups"]:,} burst/scene groups
covering {summary["grouped_images"]:,} images · hover a thumbnail for its numbers</p>
{"".join(sections)}</body></html>'''


# ---------------------------------------------------------------- driver

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('output', type=Path, help='Audit output folder (e.g. 2026-10-05).')
    ap.add_argument('--thumbs', type=Path, help='Folder holding thumbs/ and thumbs.sqlite (default: the output folder).')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--limit', type=int, help='Only measure this many pending thumbnails (for testing).')
    g = ap.add_argument_group('thresholds (re-run with new values any time; metrics are cached)')
    g.add_argument('--blur', type=float, default=0.5, help='Blur score above this is "blurry" (0-1, default 0.5).')
    g.add_argument('--dark', type=float, default=35, help='Mean luminance below this (and no highlights) is "dark".')
    g.add_argument('--bright', type=float, default=215, help='Mean luminance above this (and no shadows) is "washed out".')
    g.add_argument('--clip', type=float, default=0.15, help='Share of blown pixels above this is "blown highlights".')
    g.add_argument('--crush', type=float, default=0.40, help='Share of pure-black pixels above this is "crushed shadows".')
    g.add_argument('--flat', type=float, default=15, help='Luminance std dev below this is "low contrast".')
    g.add_argument('--burst-gap', type=float, default=2.0, help='Max seconds between frames of a burst (default 2).')
    g.add_argument('--burst-ham', type=int, default=20, help='Max dhash distance between burst frames (default 20 of 64).')
    g.add_argument('--scene-gap', type=float, default=60.0, help='Max seconds between near-duplicate frames (default 60).')
    g.add_argument('--scene-ham', type=int, default=8, help='Max dhash distance for near-duplicates (default 8 of 64).')
    g.add_argument('--keep', type=int, default=1, help='Frames to keep per group, besides your picks (default 1).')
    a = ap.parse_args()

    out = a.output.resolve()
    tdir = (a.thumbs or a.output).resolve()
    if not (tdir / 'thumbs.sqlite').exists():
        raise SystemExit(f'No thumbs.sqlite in {tdir}; run build_thumbs.py first.')

    print('Loading catalog and thumbnail manifest...', flush=True)
    imgs = load_catalog(out)
    tm = sqlite3.connect((tdir / 'thumbs.sqlite').as_uri() + '?mode=ro', uri=True)
    thumbs = {iid: upd for iid, upd in tm.execute("SELECT image_id, MAX(updated) FROM thumbs WHERE status='ok' GROUP BY 1")}
    tm.close()

    def rel(iid):
        return f'thumbs/{iid // 1000}/{iid}.jpg'

    db = sqlite3.connect(tdir / 'cull.sqlite')
    db.execute(f'''CREATE TABLE IF NOT EXISTS metrics (image_id INTEGER PRIMARY KEY, thumb_updated REAL,
        {", ".join(c + (" INTEGER" if c in ("dhash", "w", "h") else " REAL") for c in METRIC_COLS)}, error TEXT)''')
    have = dict(db.execute('SELECT image_id, thumb_updated FROM metrics'))
    pending = [(iid, str(tdir / rel(iid)), upd) for iid, upd in thumbs.items()
               if iid in imgs and have.get(iid) != upd]
    if a.limit:
        pending = pending[:a.limit]
    print(f'{len(imgs):,} master images; {len(thumbs):,} thumbnails; {len(pending):,} to measure '
          f'with {a.workers} workers.', flush=True)

    batch = []

    def flush():
        db.executemany(f'INSERT OR REPLACE INTO metrics VALUES ({",".join("?" * (len(METRIC_COLS) + 3))})', batch)
        db.commit()
        batch.clear()

    started = last = time.monotonic()
    try:
        if pending:
            with mp.Pool(a.workers) as pool:
                for i, row in enumerate(pool.imap_unordered(work, pending, chunksize=64), 1):
                    batch.append(row)
                    if len(batch) >= 1000:
                        flush()
                    now = time.monotonic()
                    if now - last > 15 or i == len(pending):
                        rate = i / max(now - started, 1e-6)
                        print(f'{i:,}/{len(pending):,}  {rate:.0f}/s  eta {(len(pending) - i) / rate / 60:.0f} min', flush=True)
                        last = now
    except KeyboardInterrupt:
        print('\nInterrupted; progress saved. Rerun the same command to resume.', flush=True)
    finally:
        if batch:
            flush()

    metrics = {}
    for row in db.execute(f"SELECT image_id, {', '.join(METRIC_COLS)} FROM metrics WHERE error = ''"):
        if row[0] in imgs and row[0] in thumbs:
            metrics[row[0]] = dict(zip(METRIC_COLS, row[1:]))

    print('Grouping bursts and scoring...', flush=True)
    res, groups = score_library(imgs, metrics, a)

    db.execute('DROP TABLE IF EXISTS cull')
    db.execute('''CREATE TABLE cull (image_id INTEGER PRIMARY KEY, verdict TEXT, flags TEXT, tech_score REAL,
        group_id INT, group_size INT, group_rank INT, lr_pick INT, lr_rating INT)''')
    db.executemany('INSERT INTO cull VALUES (?,?,?,?,?,?,?,?,?)', [
        (iid, r['verdict'], ' '.join(r['flags']), r['tech'], r['group'], r['gsize'], r['rank'],
         imgs[iid]['pick'], imgs[iid]['rating']) for iid, r in res.items()])
    db.commit()

    vc = collections.Counter(r['verdict'] for r in res.values())
    fc = collections.Counter(f for r in res.values() for f in r['flags'])
    sizes = collections.Counter(min(len(g), 10) for g in groups)
    blur = sorted(m['blur'] for m in metrics.values())
    summary = {
        'master_images': len(imgs),
        'scored': len(metrics),
        'verdicts': {k: vc.get(k, 0) for k in ('keep', 'alt', 'soft', 'exposure', 'unscored')},
        'flags': dict(fc.most_common()),
        'groups': len(groups),
        'grouped_images': sum(len(g) for g in groups),
        'group_sizes': {('10+' if k == 10 else str(k)): v for k, v in sorted(sizes.items())},
        'blur_percentiles': {str(p): round(blur[min(len(blur) - 1, int(p / 100 * len(blur)))], 3)
                             for p in (10, 25, 50, 75, 90, 95, 99)} if blur else {},
        'thresholds': {k: getattr(a, k) for k in ('blur', 'dark', 'bright', 'clip', 'crush', 'flat',
                                                    'burst_gap', 'burst_ham', 'scene_gap', 'scene_ham', 'keep')},
        'metric_errors': db.execute("SELECT COUNT(*) FROM metrics WHERE error != ''").fetchone()[0],
    }
    db.close()
    (tdir / 'cull-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (tdir / 'cull-review.html').write_text(review_html(imgs, metrics, res, groups, summary, rel), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print(f'Review page: {tdir / "cull-review.html"}')


if __name__ == '__main__':
    main()
