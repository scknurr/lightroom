"""Fold follow-up judging rounds (portfolio_round2.py + portfolio_pass.js) into the final portfolio.

  prepare <judged.json> [<judged.json> ...]  -> per-theme consolidation inputs: current frames + newcomers
  rehome  <consolidate output>               -> round-2 inputs for themes receiving frames from other themes
  apply   <round-1 output> [<round-2 output>|-] -> update portfolio_final.json (themes, strength), check accounting

Inputs and state live in WORK/portfolio/ext/.
"""
import json
import sys

import sheets
from config import WORK, final_portfolio

PF = WORK / 'portfolio'
EXT = PF / 'ext'


def result(path):
    raw = json.load(open(path))
    return raw.get('result', raw)


def slug(t):
    return ''.join(ch if ch.isalnum() else '_' for ch in t)[:30]


def build_theme(theme, ids, moved, notes, tag):
    sh = [sheets.build(ids[j:j + 9], f'{tag}_{slug(theme)}_{j // 9:02d}') for j in range(0, len(ids), 9)]
    f = EXT / f'{tag}_{slug(theme)}.json'
    f.write_text(json.dumps({'theme': theme, 'sheets': sh, 'moved_here': moved,
                             'notes': {str(i): notes[i] for i in ids}}, indent=1))
    return {'theme': theme, 'index': str(f), 'n': len(ids)}


def prepare(paths):
    EXT.mkdir(parents=True, exist_ok=True)
    final = final_portfolio()
    strength = {int(k): v for k, v in final['strength'].items()}
    judged, theme_of = {}, {}
    for p in paths:
        pool = json.loads((p.parent / 'pool.json').read_text())['themes']
        for ch in result(p)['chunks']:
            for f in ch['frames']:
                judged[f['image_id']] = f
                theme_of[f['image_id']] = pool[str(f['image_id'])]
    new = {}
    for i, f in judged.items():
        if f['keep']:
            new.setdefault(theme_of[i], []).append(i)
    notes, book = {}, set(final['book'])
    for t, ids in final['theme_order'].items():
        for i in ids:
            notes[i] = f'CURRENT portfolio frame, {strength.get(i, "?")}/10' + (' (in the 100-frame Book)' if i in book else '')
    for i, f in judged.items():
        notes[i] = f"NEW candidate, {f['strength']}/10 {f['reason']}"
    themes = []
    for t, ids in sorted(new.items()):
        ids.sort(key=lambda i: -judged[i]['strength'])
        cur = final['theme_order'].get(t, [])
        themes.append(build_theme(t, cur + ids, [], notes, 'ext'))
    all_themes = sorted(set(final['theme_order']) | set(new))
    (EXT / 'state.json').write_text(json.dumps({'judged': {str(k): v for k, v in judged.items()},
                                                'new': new, 'notes': {str(k): v for k, v in notes.items()}}))
    args = {'round': 1, 'allThemes': all_themes, 'themes': themes}
    (EXT / 'args_r1.json').write_text(json.dumps(args))
    print(f'{len(judged):,} judged, {sum(map(len, new.values()))} new keepers in {len(new)} themes')
    for t in themes:
        print(f'  {t["n"]:4d}  {t["theme"]}  (+{len(new[t["theme"]])} new)')
    print(json.dumps(args))


def rehome(path):
    r1 = result(path)
    (EXT / 'r1.json').write_text(json.dumps(r1))
    final = final_portfolio()
    st = json.loads((EXT / 'state.json').read_text())
    notes = {int(k): v for k, v in st['notes'].items()}
    out = {t['theme']: t for t in r1['themes']}
    incoming = {}
    for t in r1['themes']:
        for c in t['cut']:
            d = c.get('rehome')
            if d and d != t['theme']:
                incoming.setdefault(d, []).append(c['image_id'])
    themes = []
    for d, ids in sorted(incoming.items()):
        cur = out[d]['portfolio'] if d in out else final['theme_order'].get(d, [])
        ids = [i for i in ids if i not in cur]
        if ids:
            themes.append(build_theme(d, cur + ids, ids, notes, 'ext2'))
    print(f'{sum(map(len, incoming.values()))} frames rehomed into {len(themes)} themes')
    args = {'round': 2, 'allThemes': [], 'themes': themes}
    (EXT / 'args_r2.json').write_text(json.dumps(args))
    print(json.dumps(args))


def apply(r1path, r2path):
    r1 = result(r1path)
    r2 = result(r2path) if r2path and r2path != '-' else {'themes': []}
    final = final_portfolio()
    st = json.loads((EXT / 'state.json').read_text())
    order = dict(final['theme_order'])
    notes = dict(final['section_notes'])
    for t in r1['themes'] + r2['themes']:  # round 2 overrides round 1 for the same theme
        order[t['theme']] = t['portfolio']
        if t.get('section_note'):
            notes[t['theme']] = t['section_note']
    seen, portfolio = set(), []
    for t in list(order):
        order[t] = [i for i in order[t] if not (i in seen or seen.add(i))]
        portfolio += order[t]
        if not order[t]:
            del order[t]
    before = set(final['portfolio'])
    now = set(portfolio)
    new_kept = {i for ids in st['new'].values() for i in ids}
    # round 2 has nowhere further to send a frame, so any round-2 cut is final
    cut = {c['image_id'] for t in r1['themes'] for c in t['cut'] if not c.get('rehome')} | \
          {c['image_id'] for t in r2['themes'] for c in t['cut']}
    rehomed = {c['image_id'] for t in r1['themes'] for c in t['cut'] if c.get('rehome')}
    lost = (new_kept | before) - now - cut
    lost_rehomed = lost & rehomed
    strength = dict(final['strength'])
    strength.update({k: v['strength'] for k, v in st['judged'].items()})
    removed = before - now
    final.update(portfolio=portfolio, portfolio_set=portfolio, theme_order=order, section_notes=notes, strength=strength)
    final['book'] = [i for i in final['book'] if i in now]
    final['instagram_queue'] = [i for i in final.get('instagram_queue', []) if i in now]
    final['extension'] = {'added': sorted(now - before), 'removed': sorted(removed)}
    (PF / 'portfolio_final_before_ext.json').write_text((WORK / 'portfolio_final.json').read_text())
    (WORK / 'portfolio_final.json').write_text(json.dumps(final))
    print(f'portfolio {len(before)} -> {len(now)}: +{len(now - before)} new, -{len(removed)} displaced; '
          f'unaccounted {len(lost)} (of which lost in rehome {len(lost_rehomed)}); book {len(final["book"])}')
    if lost:
        print('UNACCOUNTED:', sorted(lost))
    for t, ids in order.items():
        print(f'  {len(ids):4d}  {t}')


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'prepare':
        from pathlib import Path
        prepare([Path(p) for p in sys.argv[2:]])
    elif cmd == 'rehome':
        rehome(sys.argv[2])
    elif cmd == 'apply':
        apply(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
