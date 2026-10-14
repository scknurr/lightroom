"""Prepare a follow-up portfolio judgment for frames found after the portfolio was finalized
(gap passes, recall sweeps, newly imported photos).

Pool: editor 'portfolio' verdicts plus backstock scored 8+ from the given editor files, plus the gap passes'
already-reviewed strong frames; minus the current portfolio, not-mine and private frames.
Theme: frames from a gap pass go to that gap's theme; everything else to the theme of its nearest current
portfolio frames (CLIP, similarity-weighted vote over the 7 nearest).
Each chunk carries a 'bar' sheet: the theme's strongest current portfolio frames, the level a newcomer must match.

  python portfolio_round2.py --editors gap sweep --out r2
"""
import argparse
import collections
import json

import numpy as np

import sheets
from config import WORK, cleared_private, final_portfolio, not_mine, work_db
from recall_sweep import load_vectors

CHUNK = 45
GAP_THEME = {'Weather': 'Weather and Sky', 'Madison': 'Madison and the Capitol'}


def slug(t):
    return ''.join(ch if ch.isalnum() else '_' for ch in t)[:30]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--editors', nargs='+', required=True, help='editor_<name>.json files to draw from')
    ap.add_argument('--out', required=True, help='output folder under WORK/portfolio')
    a = ap.parse_args()
    final = final_portfolio()
    current = set(final['portfolio'])
    foreign, cleared = not_mine(), cleared_private()
    db = work_db()
    meta = {i: (d or '', r or 0, bool(p)) for i, d, r, p in db.execute(
        'SELECT i.image_id, i.capture_time, i.rating, s.private FROM images i LEFT JOIN selection s USING(image_id)')}

    pool, private = {}, set()
    for name in a.editors:
        for p in json.loads((WORK / f'editor_{name}.json').read_text())['photos']:
            if p.get('private'):
                private.add(p['image_id'])
            if p['verdict'] == 'portfolio' or (p['verdict'] == 'backstock' and (p.get('editor_score') or 0) >= 8):
                pool[p['image_id']] = name
    forced = {}
    gap = WORK / 'portfolio' / 'gap_candidates.json'
    if 'gap' in a.editors and gap.exists():
        for g, ids in json.loads(gap.read_text()).items():
            for i in ids:
                forced[i] = GAP_THEME[g]
        for g in GAP_THEME:
            for i in json.loads((WORK / 'portfolio' / f'gap_{g}_prior.json').read_text()):
                forced[i] = GAP_THEME[g]
                pool[i] = 'gap prior'
    drop = collections.Counter()
    for i in list(pool):
        why = ('in portfolio' if i in current else 'not mine' if i in foreign else
               'private' if (i in private or meta.get(i, ('', 0, False))[2]) and i not in cleared else None)
        if why:
            drop[why] += 1
            del pool[i]

    pos, V = load_vectors()
    theme_of = {i: t for t, ids in final['theme_order'].items() for i in ids}
    ref = [i for i in theme_of if i in pos]
    # the gap passes' own picks anchor the new Weather theme for frames the sweep found
    ref += [i for i in pool if forced.get(i) == 'Weather and Sky' and i in pos]
    ref_theme = [theme_of.get(i) or forced[i] for i in ref]
    R = V[[pos[i] for i in ref]]
    themes = {}
    for i in pool:
        if i in forced:
            themes[i] = forced[i]
            continue
        if i not in pos:
            drop['no embedding'] += 1
            continue
        sims = R @ V[pos[i]]
        votes = collections.Counter()
        for k in np.argsort(-sims)[:7]:
            if ref[k] != i:
                votes[ref_theme[k]] += float(sims[k])
        themes[i] = votes.most_common(1)[0][0]

    out = WORK / 'portfolio' / a.out
    out.mkdir(parents=True, exist_ok=True)
    strength = {int(k): v for k, v in final.get('strength', {}).items()}
    by_theme = collections.defaultdict(list)
    for i, t in themes.items():
        by_theme[t].append(i)
    bars = {}
    for t in by_theme:
        best = sorted(final['theme_order'].get(t, []), key=lambda i: -strength.get(i, 0))[:9]
        if t == 'Weather and Sky':  # new theme: no current frames, so show the archive's best skies
            best = sorted((i for i in theme_of if theme_of[i] == 'Lakes, Skies and Sailing'), key=lambda i: -strength.get(i, 0))[:9]
        bars[t] = sheets.build(best, f'{a.out}_bar_{slug(t)}')['sheet'] if best else None
    chunks = []
    for t, ids in sorted(by_theme.items()):
        ids.sort(key=lambda i: (meta.get(i, ('',))[0], i))
        n = -(-len(ids) // CHUNK)
        per = -(-len(ids) // n)
        for k in range(n):
            part = ids[k * per:(k + 1) * per]
            sh = [sheets.build(part[j:j + 9], f'{a.out}_{slug(t)}_{k}_{j // 9}') for j in range(0, len(part), 9)]
            f = out / f'chunk_{len(chunks):03d}.json'
            f.write_text(json.dumps({'theme': t, 'bar': bars[t], 'sheets': sh,
                                     'starred': [i for i in part if meta.get(i, ('', 0))[1] >= 4]}, indent=1))
            chunks.append(len(part))
    calibration = json.loads((WORK / 'portfolio' / 'pool.json').read_text())['calibration']['sheet']
    args = {'n': len(chunks), 'calibration': calibration, 'dir': str(out)}
    (out / 'args.json').write_text(json.dumps(args))
    (out / 'pool.json').write_text(json.dumps({'themes': {str(i): t for i, t in themes.items()},
                                               'source': {str(i): pool[i] for i in themes}}))
    print(f'{len(themes):,} candidates in {len(by_theme)} themes -> {len(chunks)} chunks; dropped {dict(drop)}')
    for t, ids in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
        print(f'  {len(ids):5d}  {t}  (now {len(final["theme_order"].get(t, []))} in portfolio)')
    print(json.dumps(args))


if __name__ == '__main__':
    main()
