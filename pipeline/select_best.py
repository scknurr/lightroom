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
SHOOT_MIN_FRAMES = 40
SHOOT_QUOTA = 0.03
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
               i.camera, i.format, substr(i.capture_time, 1, 10), i.history_steps
        FROM images i JOIN technical t USING(image_id) JOIN clip c USING(image_id) {where}''', params).fetchall()
    if not rows:
        print('nothing scored yet')
        return
    ids = [r[0] for r in rows]
    sharp = [r[1] for r in rows]
    aes = z([r[6] for r in rows])
    taste = z([r[7] if r[7] is not None else float('nan') for r in rows])
    lsharp = z([math.log1p(s) for s in sharp])
    base = 0.45 * aes + 0.45 * taste + 0.10 * np.clip(lsharp, -2, 1.5)
    base = base - 0.6 * (np.array(sharp) < BLUR_SOFT)

    # The photographer's own signals outweigh the generic models (the aesthetic model underrates
    # documentary work: 5-star protest frames ranked below the median on the 2019-2026 run).
    pick = np.array([r[10] == 1 for r in rows], dtype=float)
    rating = np.array([r[11] or 0 for r in rows], dtype=float)
    edited = np.array([(r[18] or 0) >= 3 for r in rows], dtype=float)
    user = 0.8 * pick + 0.4 * (rating >= 3) + 0.6 * (rating >= 4) + 0.3 * edited

    # Shoot-relative term: a frame's rank within its own capture day, so each shoot's best surface
    # even when a different genre dominates the year.
    days = [r[17] or '' for r in rows]
    day_rank = np.zeros(len(rows))
    by_day = {}
    for i, d in enumerate(days):
        by_day.setdefault(d, []).append(i)
    for d, idx in by_day.items():
        if len(idx) < 2:
            continue
        order = np.argsort(np.argsort(base[idx]))
        day_rank[idx] = order / (len(idx) - 1) - 0.5
    score = base + user + 1.0 * day_rank

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

    # best-of-burst among non-rejected frames; frames the photographer rated 4+ are always kept.
    starred = {i for i, r in enumerate(rows) if (r[11] or 0) >= 4 and not reasons[r[0]]}
    best_in_burst = {}
    for i, r in enumerate(rows):
        if reasons[r[0]] or i in starred:
            continue
        b = r[5]
        if b not in best_in_burst or score[i] > score[best_in_burst[b]]:
            best_in_burst[b] = i
    burst_size = {}
    for r in rows:
        burst_size[r[5]] = burst_size.get(r[5], 0) + 1

    keepers = sorted(set(best_in_burst.values()) | starred, key=lambda i: -score[i])
    n_hero = max(1, int(len(keepers) * HERO_FRACTION))
    n_select = int(len(keepers) * SELECT_FRACTION)
    tier = {}
    for rank, i in enumerate(keepers):
        tier[i] = 'hero' if rank < n_hero else 'select' if rank < n_select else 'keep'
    # Floors: 4-5 star frames, the photographer's picks / 3+ star frames that won their burst, and the
    # best few frames of every real shoot all reach the editors (who are the real filter).
    for i in starred:
        if tier[i] == 'keep':
            tier[i] = 'select'
    for i in keepers:
        if tier[i] == 'keep' and (rows[i][10] == 1 or (rows[i][11] or 0) >= 3):
            tier[i] = 'select'
    keepers_by_day = {}
    for i in keepers:
        keepers_by_day.setdefault(days[i], []).append(i)
    for d, idx in keepers_by_day.items():
        if len(by_day.get(d, [])) < SHOOT_MIN_FRAMES:
            continue
        quota = max(3, math.ceil(SHOOT_QUOTA * len(idx)))
        for i in sorted(idx, key=lambda i: -score[i])[:quota]:
            if tier[i] == 'keep':
                tier[i] = 'select'

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

    db.execute('''CREATE TABLE IF NOT EXISTS selection (image_id INTEGER PRIMARY KEY, score REAL, tier TEXT,
                  reasons TEXT, burst INTEGER, burst_size INTEGER, private INTEGER)''')
    db.executemany('DELETE FROM selection WHERE image_id=?', [(r[0],) for r in rows])
    db.executemany('INSERT OR REPLACE INTO selection VALUES (?,?,?,?,?,?,?)', out)
    db.commit()
    from collections import Counter
    print(Counter(o[2] for o in out), 'private-flagged:', sum(o[6] for o in out))


if __name__ == '__main__':
    main()
