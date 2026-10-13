"""Build the judged-ranking groups: the top editor tier dealt across ~36-frame groups, one contact-sheet set each."""
import json
import sqlite3

import sheets
from config import WORK, cleared_private, work_db

GROUP = 36


def keepers():
    db = work_db()
    cleared = cleared_private()
    priv = {i for (i,) in db.execute('SELECT image_id FROM selection WHERE private=1')} - cleared
    model = dict(db.execute('SELECT image_id, score FROM selection'))
    recs = {}
    for f in sorted(WORK.glob('editor_*.json')):
        for p in json.loads(f.read_text())['photos']:
            recs[p['image_id']] = p
    keep = {i: p for i, p in recs.items() if p['verdict'] != 'skip' and (not p.get('private') or i in cleared) and i not in priv}
    return keep, model


def main():
    keep, model = keepers()
    top = [i for i, p in keep.items() if p['editor_score'] >= 9 or (p['editor_score'] == 8 and p['verdict'] == 'portfolio')]
    top.sort(key=lambda i: -(model.get(i) or 0))
    n = -(-len(top) // GROUP)
    groups = [top[k::n] for k in range(n)]  # deal so every group spans the quality range
    sheets.SHEETS.mkdir(parents=True, exist_ok=True)
    out = []
    for k, ids in enumerate(groups):
        sh = [sheets.build(ids[j:j + 9], f'rank_g{k:03d}_{j // 9}') for j in range(0, len(ids), 9)]
        f = WORK / 'rank' / f'group_{k:03d}.json'
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({'sheets': sh}, indent=1))
        out.append({'k': k, 'index': str(f), 'ids': ids})
    (WORK / 'rank' / 'groups.json').write_text(json.dumps(out))
    print(len(top), 'top frames ->', len(groups), 'groups')


if __name__ == '__main__':
    main()
