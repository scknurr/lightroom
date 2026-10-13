"""Merge the library editor pass into WORK/editor_library.json.

Sources (in the recovery folder written during the Codex handoff):
  editor_library.recovered.json  - every editor record from the library pass (critic corrections from
                                   completed critic batches already applied)
  additional-reviews.json        - reviews for the records the pass never returned, plus conflict fixes
  critic-completions/*.json      - corrections for the critic batches the usage limit interrupted
"""
import argparse
import json
from pathlib import Path

from config import REPO, WORK

FIELDS = {'verdict', 'duplicate_of', 'private', 'people', 'minors', 'uses', 'release_notes', 'crop',
          'title', 'caption', 'keywords', 'authorship_review'}


def apply(photos, corrections, source):
    n = 0
    for c in corrections:
        p = photos.get(c['image_id'])
        if p is None or c['field'] not in FIELDS:
            raise SystemExit(f'{source}: bad correction {c}')
        p[c['field']] = c['value']
        if c['field'] == 'duplicate_of' and c['value'] is not None:
            p['verdict'] = 'skip'
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recovery', default=str(REPO / '2026-10-12'))
    args = ap.parse_args()
    rec = Path(args.recovery)
    base = json.loads((rec / 'editor_library.recovered.json').read_text())
    photos = {p['image_id']: p for p in base['photos']}
    extra = json.loads((rec / 'additional-reviews.json').read_text())
    for p in extra['photos']:
        photos[p['image_id']] = p
    corrections = list(base.get('corrections', []))
    n_extra = apply(photos, extra.get('corrections', []), 'additional-reviews')
    corrections += extra.get('corrections', [])
    n_crit, batches = 0, 0
    for f in sorted((rec / 'critic-completions').glob('*.json')):
        r = json.loads(f.read_text())
        n_crit += apply(photos, r['corrections'], f.name)
        corrections += r['corrections']
        batches += 1
    pending = json.loads((rec / 'resume-review.json').read_text())['pending_critics']
    done = {json.loads(f.read_text())['label'] for f in (rec / 'critic-completions').glob('*.json')}
    missing = [b['label'] for b in pending if b['label'] not in done]
    if missing:
        raise SystemExit(f'critic batches still pending: {missing}')
    conflicts = [i for i, p in photos.items() if p.get('duplicate_of') and p['verdict'] != 'skip']
    if conflicts:
        raise SystemExit(f'duplicate_of set on non-skip records: {conflicts[:10]}')
    out = {'year': 'library', 'photos': list(photos.values()), 'corrections': corrections,
           'failed_batches': [], 'series': [], 'instagram_queue': [], 'notes': []}
    (WORK / 'editor_library.json').write_text(json.dumps(out, indent=1))
    from collections import Counter
    print(f'{len(photos):,} records; {n_extra} conflict fixes; {n_crit} recovered critic corrections '
          f'from {batches} batches -> {WORK / "editor_library.json"}')
    print(Counter(p['verdict'] for p in photos.values()), 'private:', sum(bool(p.get('private')) for p in photos.values()))


if __name__ == '__main__':
    main()
