"""Merge an incremental editor-pass result into WORK/editor_<year>.json.

Photos are added (new records win on conflict); series, instagram_queue and notes come from the newer
run, whose curator saw both sets.
"""
import argparse
import json

from config import WORK


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('year')
    ap.add_argument('result_json', help='workflow output file (with a "result" key) or a bare result object')
    args = ap.parse_args()
    raw = json.load(open(args.result_json))
    new = raw.get('result', raw)
    path = WORK / f'editor_{args.year}.json'
    old = json.loads(path.read_text()) if path.exists() else {'photos': [], 'corrections': []}
    by = {p['image_id']: p for p in old['photos']}
    by.update({p['image_id']: p for p in new['photos']})
    merged = {
        'year': args.year,
        'photos': list(by.values()),
        'corrections': old.get('corrections', []) + new.get('corrections', []),
        'failed_batches': new.get('failed_batches', []),
        'series': new['series'], 'instagram_queue': new['instagram_queue'], 'notes': new['notes'],
    }
    path.write_text(json.dumps(merged, indent=1))
    print(f'{len(merged["photos"])} photos, {len(merged["series"])} series -> {path}')


if __name__ == '__main__':
    main()
