"""Prepare the portfolio re-judgment: candidate pool, primary theme per frame, time-ordered chunks with
contact sheets, and a calibration sheet of the judges' top frames.

Pool: current portfolio frames, backstock scored 8+, and anything the owner rated 4-5 stars (editor score >= 6).
Each frame is judged once, in its most specific theme (the theme with the fewest series).
"""
import json

import sheets
from config import WORK, work_db
from rank_prepare import keepers

CHUNK = 45


def main():
    keep, model = keepers()
    db = work_db()
    meta = {i: (d or '', r or 0) for i, d, r in db.execute('SELECT image_id, capture_time, rating FROM images')}
    pool = [i for i, p in keep.items()
            if p['verdict'] == 'portfolio' or p['editor_score'] >= 8 or (meta.get(i, ('', 0))[1] >= 4 and p['editor_score'] >= 6)]
    cur = json.loads((WORK / 'curation_library.json').read_text())
    size = {c['name']: len(c['series']) for c in cur['collections']}
    themes_of = {}
    for c in cur['collections']:
        for s in c['series']:
            themes_of.setdefault(s, []).append(c['name'])
    primary = {}
    for s in cur['series']:
        for i in s['members']:
            for t in themes_of.get(s['name'], []):
                if i not in primary or size[t] < size[primary[i]]:
                    primary[i] = t
    by_theme = {}
    for i in pool:
        by_theme.setdefault(primary.get(i, 'Unsorted'), []).append(i)

    out = WORK / 'portfolio'
    out.mkdir(exist_ok=True)
    sheets.SHEETS.mkdir(parents=True, exist_ok=True)
    ranking = json.loads((WORK / 'ranking.json').read_text())
    calib = [r['image_id'] for r in ranking if r['basis'] == 'judges'][:9]
    cal = sheets.build(calib, 'portfolio_calibration')
    chunks = []
    for t, ids in sorted(by_theme.items()):
        ids.sort(key=lambda i: (meta.get(i, ('', 0))[0], i))
        n = -(-len(ids) // CHUNK)
        per = -(-len(ids) // n)
        for k in range(n):
            part = ids[k * per:(k + 1) * per]
            slug = ''.join(ch if ch.isalnum() else '_' for ch in t)[:30]
            sh = [sheets.build(part[j:j + 9], f'pf_{slug}_{k}_{j // 9}') for j in range(0, len(part), 9)]
            f = out / f'chunk_{len(chunks):03d}.json'
            f.write_text(json.dumps({'theme': t, 'sheets': sh}, indent=1))
            chunks.append({'c': len(chunks), 'theme': t, 'index': str(f), 'n': len(part)})
    stars = sorted(i for i in pool if meta.get(i, ('', 0))[1] >= 4)
    (out / 'pool.json').write_text(json.dumps({'pool': pool, 'primary': {str(i): primary.get(i, 'Unsorted') for i in pool},
                                               'starred': stars, 'calibration': cal}))
    args = {'chunks': chunks, 'calibration': cal['sheet']}
    (out / 'args_a.json').write_text(json.dumps(args))
    print(f'{len(pool):,} candidates in {len(by_theme)} themes -> {len(chunks)} chunks; {len(stars):,} owner-starred')
    for t, ids in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
        print(f'  {len(ids):5d}  {t}')


if __name__ == '__main__':
    main()
