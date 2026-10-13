"""Between portfolio workflow stages.

  consolidate <stage-a output>  -> per-theme sheets of everything the chunk judges kept (for theme consolidators)
  book <stage-b output>         -> sheets of each theme's top frames (for the Book panel)
"""
import json
import sys

import sheets
from config import WORK

PF = WORK / 'portfolio'


def result(path):
    raw = json.load(open(path))
    return raw.get('result', raw)


def consolidate(path):
    a = result(path)
    primary = json.loads((PF / 'pool.json').read_text())['primary']
    judged, kept = {}, {}
    for ch in a['chunks']:
        for f in ch['frames']:
            judged[f['image_id']] = f
            if f['keep']:
                kept.setdefault(primary.get(str(f['image_id']), 'Unsorted'), []).append(f)
    (PF / 'stage_a.json').write_text(json.dumps({'judged': judged, 'failed': a.get('failed', [])}))
    themes = []
    for t, fs in sorted(kept.items()):
        fs.sort(key=lambda f: -f['strength'])
        ids = [f['image_id'] for f in fs]
        slug = ''.join(ch if ch.isalnum() else '_' for ch in t)[:30]
        sh = [sheets.build(ids[j:j + 9], f'pfc_{slug}_{j // 9:02d}') for j in range(0, len(ids), 9)]
        f = PF / f'theme_{slug}.json'
        f.write_text(json.dumps({'theme': t, 'sheets': sh,
                                 'notes': {str(x['image_id']): f"{x['strength']}/10 {x['reason']}" for x in fs}}, indent=1))
        themes.append({'theme': t, 'index': str(f), 'n': len(ids)})
    (PF / 'args_b.json').write_text(json.dumps({'themes': themes}))
    print(f'{len(judged):,} judged, {sum(t["n"] for t in themes):,} kept across {len(themes)} themes')
    for t in sorted(themes, key=lambda t: -t['n']):
        print(f'  {t["n"]:4d}  {t["theme"]}')
    print(json.dumps({'themes': themes}))


def book(path, per_theme=10):
    b = result(path)
    pool, theme_of = [], {}
    for th in b['themes']:
        for i in th['portfolio'][:per_theme]:
            pool.append(i)
            theme_of[i] = th['theme']
    (PF / 'stage_b.json').write_text(json.dumps(b))
    sh = [sheets.build(pool[j:j + 9], f'pfbook_{j // 9:02d}') for j in range(0, len(pool), 9)]
    f = PF / 'book_candidates.json'
    f.write_text(json.dumps({'sheets': sh, 'theme_of': {str(k): v for k, v in theme_of.items()}}, indent=1))
    print(f'{len(pool)} book candidates in {len(sh)} sheets -> {f}')


if __name__ == '__main__':
    {'consolidate': consolidate, 'book': book}[sys.argv[1]](sys.argv[2])
