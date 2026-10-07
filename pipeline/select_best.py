"""Combine technical + aesthetic + taste signals into a score, best-of-burst, and tiers."""
import argparse
import math

import numpy as np

from config import work_db

# Sharpness = mean Laplacian variance of the 3 sharpest tiles at 1024px long side.
# Calibrated by eye on the 2019 pilot (see review page "Blur cull" section).
BLUR_HARD = 10.0
BLUR_SOFT = 25.0
HERO_FRACTION = 0.015
SELECT_FRACTION = 0.10
# Calibrated by eye on the 2019 pilot (privacy.py three-way contrasts). A flag only keeps a photo out of
# public sets; it stays a personal keeper. The editor pass adds a second, visual privacy check.
PRIVATE_NUDITY, PRIVATE_UNDERWEAR, PRIVATE_DOCUMENT = 0.97, 0.80, 0.90


def z(values):
    v = np.array(values, dtype=float)
    ok = ~np.isnan(v)
    mu, sd = v[ok].mean(), v[ok].std() or 1.0
    out = (v - mu) / sd
    out[~ok] = 0.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', action='append')
    args = ap.parse_args()
    db = work_db()
    where, params = '', []
    if args.year:
        where = f'WHERE i.year IN ({",".join("?" * len(args.year))})'
        params = args.year
    rows = db.execute(f'''
        SELECT i.image_id, t.sharp_top3, t.luma, t.clip_dark, t.clip_bright, t.burst,
               c.aesthetic, c.taste, c.junk, c.junk_p, i.pick, i.rating, c.priv_nudity, c.priv_underwear, c.priv_document,
               i.camera, i.format
        FROM images i JOIN technical t USING(image_id) JOIN clip c USING(image_id) {where}''', params).fetchall()
    if not rows:
        print('nothing scored yet')
        return
    ids = [r[0] for r in rows]
    sharp = [r[1] for r in rows]
    aes = z([r[6] for r in rows])
    taste = z([r[7] if r[7] is not None else float('nan') for r in rows])
    lsharp = z([math.log1p(s) for s in sharp])
    score = 0.45 * aes + 0.45 * taste + 0.10 * np.clip(lsharp, -2, 1.5)
    score = score - 0.6 * (np.array(sharp) < BLUR_SOFT)

    reasons = {}
    for i, r in enumerate(rows):
        iid, s, luma, dark, bright, _, _, _, junk, junk_p = r[:10]
        why = []
        if s < BLUR_HARD:
            why.append('blurry')
        if luma < 18 and dark > 0.7:
            why.append('too dark')
        if bright > 0.55:
            why.append('blown out')
        from_camera = bool(r[15]) and r[16] not in ('PNG',)
        # Zero-shot junk labels misfire on real photographs (verified on the 2019 pilot), so only
        # camera-less files (screenshots, downloads, graphics) can be junk-rejected.
        if not from_camera and junk in ('a screenshot', 'a photo of a document or receipt', 'a meme or graphic',
                    'a test shot of a gray card or color chart', 'a photo of a computer monitor') and junk_p > 0.85:
            why.append(junk.replace('a photo of ', '').replace('a ', ''))
        reasons[iid] = why

    # best-of-burst among non-rejected frames
    best_in_burst = {}
    for i, r in enumerate(rows):
        if reasons[r[0]]:
            continue
        b = r[5]
        if b not in best_in_burst or score[i] > score[best_in_burst[b]]:
            best_in_burst[b] = i
    burst_size = {}
    for r in rows:
        burst_size[r[5]] = burst_size.get(r[5], 0) + 1

    keepers = sorted(best_in_burst.values(), key=lambda i: -score[i])
    n_hero = max(1, int(len(keepers) * HERO_FRACTION))
    n_select = int(len(keepers) * SELECT_FRACTION)
    tier = {}
    for rank, i in enumerate(keepers):
        tier[i] = 'hero' if rank < n_hero else 'select' if rank < n_select else 'keep'

    out = []
    for i, r in enumerate(rows):
        iid = r[0]
        if reasons[iid]:
            t = 'reject'
        elif i in tier:
            t = tier[i]
        else:
            t = 'burst_alt'
        if t != 'reject' and r[1] < BLUR_SOFT:
            reasons[iid].append('soft')
        private = int((r[12] or 0) >= PRIVATE_NUDITY or (r[13] or 0) >= PRIVATE_UNDERWEAR
                      or (r[14] or 0) >= PRIVATE_DOCUMENT)
        out.append((iid, float(score[i]), t, ','.join(reasons[iid]), r[5], burst_size[r[5]], private))

    db.execute('DROP TABLE IF EXISTS selection')
    db.execute('''CREATE TABLE selection (image_id INTEGER PRIMARY KEY, score REAL, tier TEXT, reasons TEXT,
                  burst INTEGER, burst_size INTEGER, private INTEGER)''')
    db.executemany('INSERT OR REPLACE INTO selection VALUES (?,?,?,?,?,?,?)', out)
    db.commit()
    from collections import Counter
    print(Counter(o[2] for o in out), 'private-flagged:', sum(o[6] for o in out))


if __name__ == '__main__':
    main()
