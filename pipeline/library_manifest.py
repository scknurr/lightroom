"""One manifest for the whole catalog: the single Rescue library.

- Every scored photo: content keywords (CLIP subject/scene/light/style/event); culled frames get Rescue|Cull|<reason>.
- Every editor-reviewed photo (all editor_*.json sets): titles, captions, editor keywords, raised ratings/picks,
  and collections organized by theme, not year: Portfolio, Backstock, Series/*, Licensable, Instagram Queue, Private.
- Develop suggestions (virtual copies) for portfolio frames.
- The in-place import list for photos that were never cataloged.
"""
import argparse
import json

from config import WORK, apply_final_verdict, cleared_private, final_portfolio, not_mine, work_db

LICENSE_OK_PEOPLE = {'none', 'incidental'}
TAG_MIN = 0.30
TAG_SECOND = 0.45


def clean(s):
    return s.replace('|', '/').replace('/', '-').strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='library-001')
    ap.add_argument('--no-import', action='store_true')
    args = ap.parse_args()

    editor, series_of, ig_queue = {}, {}, []
    for f in sorted(WORK.glob('editor_*.json')):
        e = json.loads(f.read_text())
        for p in e['photos']:
            editor[p['image_id']] = p
        for s in e.get('series', []):
            for iid in s['members']:
                series_of.setdefault(iid, set()).add(s['name'])
        ig_queue += [i for i in e.get('instagram_queue', []) if i not in ig_queue]
    cur = WORK / 'curation_library.json'
    if cur.exists():
        # Library curation: year-level series nested under library-wide themes -> Themes/<Theme>/<Series>.
        c = json.loads(cur.read_text())
        themes_of = {}
        for col in c.get('collections', []):
            for sname in col['series']:
                themes_of.setdefault(sname, []).append(col['name'])
        series_of = {}
        for s in c['series']:
            for theme in themes_of.get(s['name'], ['Other']):
                for iid in s['members']:
                    series_of.setdefault(iid, set()).add(f'{clean(theme)}/{clean(s["name"])}')
        ig_queue = c.get('instagram_queue', ig_queue)
        series_prefix = 'Themes'
    else:
        series_prefix = 'Series'
    final = final_portfolio()
    if final and final.get('instagram_queue'):
        ig_queue = final['instagram_queue']
    ig = set(ig_queue)
    book = set((final or {}).get('book', []))
    editor = {i: apply_final_verdict(i, p, final) for i, p in editor.items()}

    db = work_db()
    cleared = cleared_private()
    foreign = not_mine()
    photos, counts = [], {}
    for iid, uuid, path, tier, reasons, private_flag, tags in db.execute('''
            SELECT i.image_id, i.uuid, i.path, s.tier, s.reasons, s.private, c.tags
            FROM selection s JOIN images i USING(image_id) JOIN clip c USING(image_id)'''):
        kws = set()
        for cat, items in json.loads(tags or '{}').items():
            for k, (t, p) in enumerate(items[:2]):
                if p >= (TAG_MIN if k == 0 else TAG_SECOND):
                    kws.add(f'Rescue|{cat.title()}|{clean(t)}')
        if tier == 'reject':
            for r in (reasons or '').split(','):
                if r and r != 'soft':
                    kws.add(f'Rescue|Cull|{clean(r)}')
        rec = {'uuid': uuid, 'image_id': iid, 'path': path, 'collections': []}
        if iid in foreign:
            # Not the owner's photo: tag and collect for him to delete or relocate; never rate, title or publish it.
            kws.add('Rescue|Cull|not my photo')
            rec['collections'] = ['Not Mine']
            rec['keywords'] = sorted(kws)
            photos.append(rec)
            counts['Not Mine'] = counts.get('Not Mine', 0) + 1
            continue
        e = editor.get(iid)
        if e and e['verdict'] != 'skip':
            private = (bool(private_flag) or e.get('private', False)) and iid not in cleared
            verdict = e['verdict']
            kws |= {f'Rescue|Tags|{clean(k.lower())}' for k in e.get('keywords', [])}
            rec['rating'] = 5 if iid in book else 4 if verdict == 'portfolio' else 3
            rec['pick'] = verdict == 'portfolio'
            for fld in ('title', 'caption'):
                if e.get(fld):
                    rec[fld] = e[fld]
            if private:
                rec['collections'] = ['Private']
                rec['color_label'] = 'purple'
            else:
                rec['collections'] = ['Portfolio' if verdict == 'portfolio' else 'Backstock'] + (['Book'] if iid in book else [])
                rec['collections'] += [f'{series_prefix}/{n}' if '/' in n else f'{series_prefix}/{clean(n)}'
                                       for n in sorted(series_of.get(iid, []))]
                if 'stock' in e.get('uses', []) and e.get('people') in LICENSE_OK_PEOPLE and not e.get('minors'):
                    rec['collections'].append('Licensable')
                if iid in ig and not e.get('minors'):
                    rec['collections'].append('Instagram Queue')
            dev = {}
            if e.get('crop'):
                dev['crop'] = e['crop']
            if (e.get('treatment') or {}).get('settings'):
                dev['settings'] = e['treatment']['settings']
            if dev and verdict == 'portfolio' and not private:
                rec['develop_suggestion'] = dev
        elif private_flag and iid not in cleared:
            rec['collections'] = ['Private']
        if not kws and not rec['collections'] and 'rating' not in rec:
            continue
        rec['keywords'] = sorted(kws)
        photos.append(rec)
        for c_ in rec['collections']:
            top = c_.split('/')[0]
            counts[top] = counts.get(top, 0) + 1

    manifest = {'version': 1, 'run_id': args.run_id, 'collection_set': 'Rescue', 'photos': photos}
    if not args.no_import:
        src = WORK / 'import_list_all.json'
        files = json.loads((src if src.exists() else WORK / 'import_list.json').read_text())
        manifest['import'] = {'folder_note': 'Own-camera photos that were never cataloged (Pixel 5 roll, G DRIVE project folders, Dropbox/Documents shoots). Added in place.',
                              'files': sorted(files)}
    out = WORK / 'manifest_library.json'
    out.write_text(json.dumps(manifest, separators=(',', ':')))
    print(f'{len(photos):,} photos (+{len(manifest.get("import", {}).get("files", [])):,} to import) -> {out}'
          f' ({out.stat().st_size / 1e6:.0f} MB)')
    for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f'  {n:7,d}  {k}')


if __name__ == '__main__':
    main()
