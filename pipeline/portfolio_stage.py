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


if __name__ == '__main__' and sys.argv[1] in ('consolidate', 'book'):
    {'consolidate': consolidate, 'book': book}[sys.argv[1]](sys.argv[2])


def rehome(path):
    """Round 2 inputs: each theme that receives rehomed frames gets its round-1 keepers plus the newcomers."""
    r1 = result(path)
    (PF / 'stage_b_r1.json').write_text(json.dumps(r1))
    names = {t['theme'] for t in r1['themes']}
    incoming = {}
    for t in r1['themes']:
        for c in t['cut']:
            dest = c.get('rehome')
            if dest and dest in names and dest != t['theme']:
                incoming.setdefault(dest, []).append(c['image_id'])
    a = json.loads((PF / 'stage_a.json').read_text())['judged']
    themes = []
    for t in r1['themes']:
        new = [i for i in incoming.get(t['theme'], []) if i not in t['portfolio']]
        if not new:
            continue
        ids = t['portfolio'] + new
        slug = ''.join(ch if ch.isalnum() else '_' for ch in t['theme'])[:30]
        sh = [sheets.build(ids[j:j + 9], f'pfr2_{slug}_{j // 9:02d}') for j in range(0, len(ids), 9)]
        f = PF / f'theme2_{slug}.json'
        f.write_text(json.dumps({'theme': t['theme'], 'sheets': sh, 'moved_here': new,
                                 'notes': {str(i): f"{a[str(i)]['strength']}/10 {a[str(i)]['reason']}" for i in ids}}, indent=1))
        themes.append({'theme': t['theme'], 'index': str(f), 'n': len(ids)})
    moved = sum(len(v) for v in incoming.values())
    print(f'{moved} frames rehomed into {len(themes)} themes')
    print(json.dumps({'round': 2, 'allThemes': [], 'themes': themes}))


def merge(r2path):
    """Merge round 1 and round 2 into stage_b.json and check every stage-A keeper is accounted for."""
    r1 = json.loads((PF / 'stage_b_r1.json').read_text())
    r2 = result(r2path) if r2path != '-' else {'themes': []}
    by2 = {t['theme']: t for t in r2['themes']}
    themes, placed = [], {}
    for t in r1['themes']:
        u = by2.get(t['theme'], t)
        themes.append({'theme': t['theme'], 'portfolio': u['portfolio'], 'section_note': u.get('section_note', ''),
                       'cut': [c for c in t['cut'] if not c.get('rehome')] + (u['cut'] if t['theme'] in by2 else [])})
        for i in u['portfolio']:
            placed[i] = placed.get(i, 0) + 1
    dup = [i for i, n in placed.items() if n > 1]
    seen = set()
    for t in themes:  # a frame kept in two themes stays only in its first occurrence
        keep = []
        for i in t['portfolio']:
            if i not in seen:
                seen.add(i)
                keep.append(i)
        t['portfolio'] = keep
    a = json.loads((PF / 'stage_a.json').read_text())['judged']
    kept_a = {int(k) for k, v in a.items() if v['keep']}
    final = {i for t in themes for i in t['portfolio']}
    cut = {c['image_id'] for t in themes for c in t['cut']}
    unaccounted = kept_a - final - cut
    out = {'themes': themes}
    (PF / 'stage_b_merged.json').write_text(json.dumps(out))
    print(f'final portfolio {len(final)} | cut {len(cut - final)} | in two themes (deduped) {len(dup)} | unaccounted {len(unaccounted)}')
    if unaccounted:
        print('UNACCOUNTED:', sorted(unaccounted)[:20])


if __name__ == '__main__' and sys.argv[1] in ('rehome', 'merge'):
    {'rehome': rehome, 'merge': merge}[sys.argv[1]](sys.argv[2])
