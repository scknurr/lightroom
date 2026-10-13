"""Display and checkpoint the recovered review backlog; never touches Lightroom."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / '2026-10-12'


def data():
    records = json.loads((ROOT / 'editor_library.recovered.json').read_text())
    resume = json.loads((ROOT / 'resume-review.json').read_text())
    return {p['image_id']: p for p in records['photos']}, {b['label']: b for b in resume['pending_critics']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['show', 'save', 'status'])
    ap.add_argument('label', nargs='?')
    ap.add_argument('--corrections', default='[]', help='JSON corrections: image_id, field, value, reason')
    ap.add_argument('--notes', default='')
    ap.add_argument('--review-date', default=datetime.now(ZoneInfo('America/Chicago')).date().isoformat())
    a = ap.parse_args()
    photos, batches = data()
    dest = ROOT / 'critic-completions'
    if a.action == 'status':
        done = {json.loads(p.read_text())['label'] for p in dest.glob('*.json')}
        print(json.dumps({'complete': len(done), 'remaining': [k for k in batches if k not in done]}))
        return
    b = batches[a.label]
    if a.action == 'show':
        for sheet in b['sheets']:
            print(sheet['sheet'])
            for slot, iid in sheet['slots'].items():
                p = photos[iid]
                print(slot, json.dumps([iid, p['verdict'], p['duplicate_of'], p['private'],
                    p['people'], p['minors'], p['uses'], p['release_notes'], p['crop'], p['title']], separators=(',', ':')))
        return
    corrections = json.loads(a.corrections)
    allowed = {'verdict', 'duplicate_of', 'private', 'people', 'minors', 'uses', 'release_notes', 'crop', 'title', 'caption', 'keywords', 'authorship_review'}
    seen = set()
    for c in corrections:
        assert c['image_id'] in b['image_ids'], c
        assert c['field'] in allowed and c.get('reason'), c
        key = c['image_id'], c['field']
        assert key not in seen, key
        seen.add(key)
        if c['field'] == 'duplicate_of' and c['value'] is not None:
            assert c['value'] in photos and c['value'] != c['image_id'], c
    dest.mkdir(exist_ok=True)
    target = dest / (a.label.replace(':', '-').replace(' ', '_') + '.json')
    record = {'label': a.label, 'reviewer': 'Codex', 'review_date': a.review_date,
              'reviewed_sheets': [s['sheet'] for s in b['sheets']], 'image_ids': b['image_ids'],
              'corrections': corrections, 'notes': a.notes,
              'scope': 'Visual review of the listed contact sheets; original-resolution focus and licensing rights are not verified.'}
    with target.open('x') as f:
        json.dump(record, f, indent=2)
    print(f"Saved {a.label}: {len(b['image_ids'])} photos, {len(corrections)} corrections")


if __name__ == '__main__':
    main()
