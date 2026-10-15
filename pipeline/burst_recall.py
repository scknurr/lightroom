"""Burst recall: frames demoted to 'burst_alt' were never shown to an editor. The best moment of a burst is
often not the sharpest or highest-scored frame (a leaf landing on a dog's face), and dedupe.py can shelve a
whole burst as a near-duplicate of another one.

Candidates (unreviewed burst_alt frames; not foreign, not private unless cleared):
  lost     bursts with no kept or reviewed member: each distinct frame, when it differs from every
           reviewed frame of that day (CLIP cosine < SAME) or outscores the reviewed frame it resembles
  variant  alternates of bursts whose reviewed member is strong (portfolio or editor score >= 7), when they
           differ from it (cosine < SAME)
  owner    alternates the photographer picked or rated 3+
Near-duplicates among the candidates are collapsed per burst. Writes WORK/portfolio/burst_candidates.json.
"""
import collections
import json

import numpy as np

from config import WORK, cleared_private, not_mine, work_db
from recall_sweep import load_vectors

SAME = 0.965   # cosine at/above which two frames of a shoot read as the same picture
PER_BURST = 3  # at most this many alternates from one burst


def main():
    db = work_db()
    ed = {}
    for f in WORK.glob('editor_*.json'):
        for p in json.loads(f.read_text())['photos']:
            ed[p['image_id']] = p
    foreign, cleared = not_mine(), cleared_private()
    rows = db.execute('''SELECT s.image_id, s.tier, s.score, s.burst, s.private, i.rating, i.pick,
                                substr(i.capture_time,1,10) FROM selection s JOIN images i USING(image_id)''').fetchall()
    pos, V = load_vectors()
    ok = lambda r: r[0] not in foreign and (not r[4] or r[0] in cleared) and r[0] in pos
    by_burst, reviewed_by_day = collections.defaultdict(list), collections.defaultdict(list)
    for r in rows:
        if not ok(r):
            continue
        if r[3] is not None:
            by_burst[r[3]].append(r)
        if r[0] in ed and ed[r[0]]['verdict'] != 'skip':
            reviewed_by_day[r[7]].append(r)
    vec = lambda i: V[pos[i]]
    score = {r[0]: r[2] or 0 for r in rows}

    found = collections.defaultdict(set)
    for b, ms in by_burst.items():
        alts = sorted((m for m in ms if m[1] == 'burst_alt' and m[0] not in ed), key=lambda m: -(m[2] or 0))
        if not alts:
            continue
        reviewed = [m for m in ms if m[0] in ed]
        if not reviewed and not any(m[1] in ('keep', 'hero', 'select') for m in ms):
            # every distinct moment of a shelved burst, not just its top-scored frame (collapsed below)
            day = reviewed_by_day.get(alts[0][7], [])
            for m in alts:
                sims = [(float(vec(m[0]) @ vec(d[0])), d[0]) for d in day]
                near = max(sims, default=(0, None))
                if near[0] < SAME or score[m[0]] > score.get(near[1], 0):
                    found['lost'].add(m[0])
        strong = [m for m in reviewed if ed[m[0]]['verdict'] == 'portfolio' or (ed[m[0]].get('editor_score') or 0) >= 7]
        if strong:
            for m in alts:
                if all(float(vec(m[0]) @ vec(s[0])) < SAME for s in strong):
                    found['variant'].add(m[0])
        for m in alts:
            if (m[5] or 0) >= 3 or m[6] == 1:
                found['owner'].add(m[0])

    burst_of = {r[0]: r[3] for r in rows}
    cand = set().union(*found.values())
    per = collections.defaultdict(list)
    for i in cand:
        per[burst_of[i]].append(i)
    keep_ids = []
    for b, ids in per.items():
        ids.sort(key=lambda i: -score[i])
        kept = []
        for i in ids:
            if len(kept) < PER_BURST and all(float(vec(i) @ vec(j)) < SAME for j in kept):
                kept.append(i)
        keep_ids += kept
    day = {r[0]: r[7] for r in rows}
    out = {'candidates': sorted(keep_ids, key=lambda i: (day[i], i)),
           'methods': {str(i): sorted(m for m, s in found.items() if i in s) for i in keep_ids}}
    (WORK / 'portfolio' / 'burst_candidates.json').write_text(json.dumps(out))
    years = collections.Counter((day[i] or '')[:4] for i in keep_ids)
    print('per method:', {m: len(s) for m, s in found.items()}, '| union', len(cand), '| after per-burst dedupe', len(keep_ids))
    print('by year:', dict(sorted(years.items())))
    print('includes 367724:', 367724 in keep_ids or '(already reviewed)')


if __name__ == '__main__':
    main()
