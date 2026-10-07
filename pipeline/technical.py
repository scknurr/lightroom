"""Technical metrics per thumbnail (sharpness, exposure, color, pHash) and burst grouping."""
import argparse
from multiprocessing import Pool

import cv2
import imagehash
import numpy as np
from PIL import Image

from config import thumb_path, work_db

GRID = 6


def metrics(iid):
    try:
        img = cv2.imread(str(thumb_path(iid)))
        if img is None:
            return None
        h, w = img.shape[:2]
        s = 1024 / max(h, w)
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(g, cv2.CV_64F)
        H, W = g.shape
        tiles = [lap[r * H // GRID:(r + 1) * H // GRID, c * W // GRID:(c + 1) * W // GRID].var()
                 for r in range(GRID) for c in range(GRID)]
        tiles.sort()
        b, gr, r = [x.astype(np.float64) for x in cv2.split(img)]
        rg, yb = r - gr, 0.5 * (r + gr) - b
        color = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())
        ph = str(imagehash.phash(Image.fromarray(g)))
        return (iid, float(lap.var()), float(tiles[-1]), float(np.mean(tiles[-3:])), float(np.median(tiles)),
                float(g.mean()), float((g < 8).mean()), float((g > 247).mean()), float(g.std()),
                float(color), ph)
    except Exception:
        return None


def bursts(db, gap_seconds=3, max_hamming=20):
    rows = db.execute('''SELECT i.image_id, i.capture_time, t.phash FROM images i JOIN technical t USING(image_id)
                         WHERE i.capture_time != '' ORDER BY i.capture_time, i.image_id''').fetchall()
    import datetime as dt

    def ts(s):
        try:
            return dt.datetime.fromisoformat(s[:19]).timestamp()
        except ValueError:
            return None
    group, prev, out = 0, None, []
    for iid, cap, ph in rows:
        t = ts(cap)
        if prev is None or t is None or prev[0] is None or t - prev[0] > gap_seconds or \
                imagehash.hex_to_hash(ph) - imagehash.hex_to_hash(prev[1]) > max_hamming:
            group += 1
        out.append((group, iid))
        prev = (t, ph)
    db.executemany('UPDATE technical SET burst=? WHERE image_id=?', out)
    db.commit()
    sizes = db.execute('SELECT COUNT(*) FROM (SELECT burst FROM technical GROUP BY burst HAVING COUNT(*)>1)').fetchone()[0]
    print(f'{group:,} groups; {sizes:,} multi-frame bursts')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=14)
    args = ap.parse_args()
    db = work_db()
    db.execute('''CREATE TABLE IF NOT EXISTS technical (image_id INTEGER PRIMARY KEY, lap_var REAL, sharp_max REAL,
                  sharp_top3 REAL, sharp_median REAL, luma REAL, clip_dark REAL, clip_bright REAL, contrast REAL,
                  colorfulness REAL, phash TEXT, burst INTEGER)''')
    todo = [r[0] for r in db.execute('''SELECT image_id FROM thumbs WHERE source NOT IN ('fail','skip_video')
                                        AND image_id NOT IN (SELECT image_id FROM technical)''')]
    print(f'{len(todo):,} to measure', flush=True)
    with Pool(args.workers) as pool:
        for n, res in enumerate(pool.imap_unordered(metrics, todo, chunksize=32), 1):
            if res:
                db.execute('INSERT OR REPLACE INTO technical VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)', res)
            if n % 2000 == 0:
                db.commit()
                print(f'{n:,}/{len(todo):,}', flush=True)
    db.commit()
    bursts(db)


if __name__ == '__main__':
    main()
