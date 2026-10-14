"""Recall sweep: find likely portfolio winners among photos no editor has reviewed.

Methods (each independent, union taken):
  shoot     best few unreviewed frames of every shooting day (shoot-relative score)
  lookalike nearest unreviewed neighbors of final-portfolio frames, from other days
  genre     CLIP "excellent photograph" prompts per genre
  owner     unreviewed frames the owner picked, rated 3+ or edited
Near-duplicates within the same day are collapsed. Writes WORK/portfolio/sweep_candidates.json.

--imports restricts the sweep to the uncataloged photos (pseudo ids), takes a larger share of every shoot
(they were never seen by an editor) and always includes the ids listed in --force (a json list of file paths).
Writes WORK/portfolio/imports_candidates.json.
"""
import argparse
import collections
import json
import math

import numpy as np

from config import WORK, cleared_private, final_portfolio, not_mine, work_db

GENRE = [
    'an award-winning documentary photograph capturing a decisive moment',
    'a striking portrait with beautiful dramatic light and strong expression',
    'a stunning landscape photograph with dramatic light and sky',
    'a powerful black and white fine art photograph',
    'an exciting sports action photograph frozen at the peak moment',
    'a beautiful character portrait of a dog',
    'a moody night photograph with city lights and reflections',
    'a funny candid moment of people laughing',
    'a graphic minimalist photograph with bold color and shapes',
    'golden hour light glowing on people outdoors',
    'an intimate emotional moment between two people',
    'a dramatic travel photograph of a remarkable place',
]


def load_vectors():
    ids, vecs = [], []
    for s in sorted((WORK / 'embeddings').glob('shard_*[0-9].npy')):
        ids.append(np.load(str(s).replace('.npy', '_ids.npy')))
        vecs.append(np.load(s).astype(np.float32))
    ids = np.concatenate(ids)
    v = np.concatenate(vecs)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    last = {int(i): k for k, i in enumerate(ids)}  # later shards win
    return last, v


def text_vectors(prompts):
    import open_clip
    import torch
    m, _, _ = open_clip.create_model_and_transforms('ViT-L-14', pretrained='openai', cache_dir=str(WORK / 'models' / 'clip'))
    tok = open_clip.get_tokenizer('ViT-L-14')
    with torch.no_grad():
        t = m.encode_text(tok(prompts))
        return (t / t.norm(dim=-1, keepdim=True)).numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--imports', action='store_true')
    ap.add_argument('--force', help='json list of file paths to include regardless of score (imports mode)')
    a = ap.parse_args()
    share, floor = (0.05, 3) if a.imports else (0.015, 2)
    db = work_db()
    reviewed = set()
    for f in WORK.glob('editor_*.json'):
        reviewed |= {p['image_id'] for p in json.loads(f.read_text())['photos']}
    gap = WORK / 'portfolio' / 'gap_candidates.json'
    if gap.exists():
        reviewed |= {i for ids in json.loads(gap.read_text()).values() for i in ids}
    if a.imports:
        sweep = WORK / 'portfolio' / 'sweep_candidates.json'
        if sweep.exists():
            reviewed |= set(json.loads(sweep.read_text())['candidates'])
    foreign, cleared = not_mine(), cleared_private()
    final = final_portfolio()
    rows = db.execute('''SELECT s.image_id, s.score, s.tier, s.private, substr(i.capture_time,1,10), i.year,
                                i.pick, i.rating, i.history_steps
                         FROM selection s JOIN images i USING(image_id)''').fetchall()
    elig = {r[0]: r for r in rows if r[2] in ('keep', 'hero', 'select') and r[0] not in reviewed
            and r[0] not in foreign and (not r[3] or r[0] in cleared)}
    forced = set()
    if a.imports:
        elig = {i: r for i, r in elig.items() if i >= 900_000_000}
        if a.force:
            by_path = {p: int(i) for i, p in json.loads((WORK / 'imports_map.json').read_text()).items()}
            forced = {by_path[p] for p in json.loads(open(a.force).read()) if p in by_path}
            for r in rows:
                if r[0] in forced and r[2] != 'reject' and r[0] not in foreign:
                    elig[r[0]] = r
    pos, V = load_vectors()
    elig = {i: r for i, r in elig.items() if i in pos}
    found = collections.defaultdict(set)

    by_day = collections.defaultdict(list)
    for i, r in elig.items():
        by_day[r[4]].append(i)
    for d, ids in by_day.items():
        if len(ids) < 8:
            continue
        n = max(floor, math.ceil(share * len(ids)))
        for i in sorted(ids, key=lambda i: -elig[i][1])[:n]:
            found['shoot'].add(i)

    E = np.array(sorted(elig))
    EV = V[[pos[i] for i in E]]
    eday = np.array([elig[i][4] for i in E])
    day_of = dict(db.execute('SELECT image_id, substr(capture_time,1,10) FROM images'))
    for p in final['portfolio']:
        if p not in pos:
            continue
        sims = EV @ V[pos[p]]
        for k in np.argsort(-sims)[:12]:
            if sims[k] < 0.82:
                break
            if eday[k] != day_of.get(p):
                found['lookalike'].add(int(E[k]))
    T = text_vectors(GENRE)
    for t in T:
        sims = EV @ t
        for k in np.argsort(-sims)[:220]:
            found['genre'].add(int(E[k]))
    for i, r in elig.items():
        if r[6] == 1 or (r[7] or 0) >= 3 or (r[8] or 0) >= 3:
            found['owner'].add(i)

    found['forced'] = {i for i in forced if i in elig}
    cand = set().union(*found.values())
    # collapse near-duplicates within a day: keep the better-scored frame
    keep_ids = []
    byd = collections.defaultdict(list)
    for i in cand:
        byd[elig[i][4]].append(i)
    for d, ids in byd.items():
        ids.sort(key=lambda i: -elig[i][1])
        kept = []
        for i in ids:
            if i in forced or all(float(V[pos[i]] @ V[pos[j]]) < 0.95 for j in kept):
                kept.append(i)
        keep_ids += kept
    methods = {i: sorted(m for m, s in found.items() if i in s) for i in keep_ids}
    years = collections.Counter((elig[i][5] if (elig[i][5] or '') >= '2008' else 'pre-2008') for i in keep_ids)
    out = {'candidates': sorted(keep_ids, key=lambda i: (elig[i][4], i)), 'methods': {str(k): v for k, v in methods.items()}}
    (WORK / 'portfolio' / ('imports_candidates.json' if a.imports else 'sweep_candidates.json')).write_text(json.dumps(out))
    print('eligible unreviewed:', len(elig))
    print('per method:', {m: len(s) for m, s in found.items()}, '| union', len(cand), '| after same-day dedupe', len(keep_ids))
    print('by year:', dict(sorted(years.items())))
    print('uncataloged among candidates:', sum(1 for i in keep_ids if i >= 900_000_000))


if __name__ == '__main__':
    main()
