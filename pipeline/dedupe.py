"""Collapse near-duplicate hero/select frames that burst grouping missed (same scene re-shot minutes apart).

Two frames are duplicates when their CLIP embeddings are very similar and they were captured within
WINDOW seconds. The higher-scored frame keeps its tier; the others become 'burst_alt' with reason 'near-dup'.
"""
import argparse
import datetime as dt

import numpy as np

from config import WORK, work_db

SIM = 0.945  # calibrated by eye on 2019; 0.93-0.94 still merged different compositions
WINDOW = 15 * 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', required=True)
    args = ap.parse_args()
    db = work_db()
    rows = db.execute('''SELECT s.image_id, s.score, i.capture_time, c.shard, c.row, t.w > t.h, i.rating
                         FROM selection s JOIN images i USING(image_id) JOIN clip c USING(image_id)
                         JOIN thumbs t USING(image_id)
                         WHERE i.year=? AND s.tier IN ('hero','select') AND c.shard IS NOT NULL
                         ORDER BY i.capture_time''', (args.year,)).fetchall()
    shards = {}
    vecs = []
    for _, _, _, shard, row, _, _ in rows:
        if shard not in shards:
            shards[shard] = np.load(WORK / 'embeddings' / f'{shard}.npy').astype(np.float32)
        vecs.append(shards[shard][row])
    if not rows:
        return
    v = np.stack(vecs)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    ts = [dt.datetime.fromisoformat(r[2][:19]).timestamp() if r[2] else None for r in rows]
    demoted = set()
    for i in range(len(rows)):
        if rows[i][0] in demoted or ts[i] is None:
            continue
        for j in range(i + 1, len(rows)):
            if ts[j] is None or ts[j] - ts[i] > WINDOW:
                break
            # A vertical and a horizontal take are both worth keeping (stock, Instagram crops).
            if rows[j][0] in demoted or rows[j][5] != rows[i][5] or float(v[i] @ v[j]) < SIM:
                continue
            loser = rows[j][0] if rows[i][1] >= rows[j][1] else rows[i][0]
            if (rows[[i, j][loser == rows[j][0]]][6] or 0) >= 4:
                continue  # never demote a frame the photographer rated 4+
            demoted.add(loser)
            if loser == rows[i][0]:
                break
    db.executemany('''UPDATE selection SET tier='burst_alt',
                      reasons=CASE WHEN reasons='' THEN 'near-dup' ELSE reasons || ',near-dup' END
                      WHERE image_id=?''', [(d,) for d in demoted])
    db.commit()
    print(f'{args.year}: {len(rows):,} hero/select checked, {len(demoted):,} near-duplicates demoted')


if __name__ == '__main__':
    main()
