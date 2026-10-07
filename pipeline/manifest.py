"""Build the Lightroom plugin manifest from selection scores + editor-pass decisions.

Input:  WORK/editor_<year>.json  ({"photos": [...editor records...], "series": [...]})
Output: WORK/manifest_<year>.json  (format documented in lightroom-plugin/README.md)
"""
import argparse
import json

from config import WORK, work_db

LICENSE_OK_PEOPLE = {'none', 'incidental'}


def clean(s):
    return s.replace('|', '/').strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', default='2019')
    ap.add_argument('--run-id')
    args = ap.parse_args()
    y = args.year
    editor = json.loads((WORK / f'editor_{y}.json').read_text())
    by_id = {p['image_id']: p for p in editor['photos']}
    ig_queue = set(editor.get('instagram_queue', []))
    series_of = {}
    for s in editor.get('series', []):
        for iid in s['members']:
            series_of.setdefault(iid, []).append(s['name'])

    db = work_db()
    rows = db.execute('''SELECT i.image_id, i.uuid, i.path, s.tier, s.private, c.tags, c.sensitive
                         FROM selection s JOIN images i USING(image_id) JOIN clip c USING(image_id)
                         WHERE i.year=? AND s.tier IN ('hero','select')''', (y,)).fetchall()
    photos = []
    counts = {}
    for iid, uuid, path, tier, private_flag, tags, sensitive in rows:
        e = by_id.get(iid)
        if e is None:
            continue
        private = bool(private_flag) or e.get('private', False)
        verdict = e['verdict']
        if verdict == 'skip':
            continue
        kws = []
        for cat, items in json.loads(tags or '{}').items():
            for t, p in items[:1]:
                if p >= 0.25:
                    kws.append(f'Rescue|{cat.title()}|{clean(t)}')
        kws += [f'Rescue|Tags|{clean(k.lower())}' for k in e.get('keywords', [])]
        cols = []
        if private:
            cols.append(f'Private/{y}')
        else:
            cols.append(f'Heroes/{y}' if verdict == 'portfolio' else f'Backstock/{y}')
            cols += [f'Series/{clean(n)}' for n in series_of.get(iid, [])]
            uses = set(e.get('uses', []))
            if 'stock' in uses and e.get('people') in LICENSE_OK_PEOPLE and not e.get('minors'):
                cols.append('Licensable/Candidates')
            if iid in ig_queue:
                cols.append('Instagram/Queue')
        rec = {
            'uuid': uuid, 'image_id': iid, 'path': path,
            'keywords': sorted(set(kws)),
            'rating': 4 if verdict == 'portfolio' else 3,
            'pick': verdict == 'portfolio',
            'collections': cols,
        }
        if private:
            rec['color_label'] = 'purple'
        if e.get('title'):
            rec['title'] = e['title']
        if e.get('caption'):
            rec['caption'] = e['caption']
        dev = {}
        if e.get('crop'):
            dev['crop'] = e['crop']
        if e.get('treatment', {}).get('settings'):
            dev['settings'] = e['treatment']['settings']
        if e.get('treatment', {}).get('graduated_filters'):
            dev['graduated_filters'] = e['treatment']['graduated_filters']
        if dev and verdict == 'portfolio':
            rec['develop_suggestion'] = dev
        photos.append(rec)
        for c in cols:
            counts[c] = counts.get(c, 0) + 1

    manifest = {'version': 1, 'run_id': args.run_id or f'{y}-pilot', 'collection_set': 'Rescue', 'photos': photos}
    out = WORK / f'manifest_{y}.json'
    out.write_text(json.dumps(manifest, indent=1))
    print(f'{len(photos):,} photos -> {out}')
    for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:25]:
        print(f'  {n:5d}  {c}')


if __name__ == '__main__':
    main()
