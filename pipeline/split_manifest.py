"""Split manifest_library.json into the runs the Lightroom plugin is given, in order:

  00_pilot.json        3 photos (one plain, one Book frame with a develop suggestion, one portfolio frame)
  01_import.json       import block only: not-yet-catalogued files, minus photos marked not-mine
  02_tags_<k>.json     catalogued photos, CHUNK per file
  09_tags_imported.json photos added by 01_import (path-only records); run after the import

Writes WORK/apply/. Every file keeps the manifest's run_id, so re-running any of them is idempotent.
"""
import json

from config import WORK, final_portfolio, not_mine

CHUNK = 40_000


def main():
    m = json.loads((WORK / 'manifest_library.json').read_text())
    out = WORK / 'apply'
    out.mkdir(exist_ok=True)
    for f in out.glob('*.json'):
        f.unlink()
    head = {k: m[k] for k in ('version', 'run_id', 'collection_set')}
    photos = m['photos']
    catalogued = [p for p in photos if p.get('uuid') or p.get('image_id')]
    imported = [p for p in photos if not (p.get('uuid') or p.get('image_id'))]

    book = set(final_portfolio()['book'])
    plain = next(p for p in catalogued if p.get('keywords') and not p.get('collections'))
    bookdev = next(p for p in catalogued if p.get('image_id') in book and p.get('develop_suggestion'))
    port = next(p for p in catalogued if 'Portfolio' in p.get('collections', []) and p.get('image_id') not in book)
    files = {}

    def write(name, body):
        (out / name).write_text(json.dumps({**head, **body}, separators=(',', ':')))
        files[name] = body

    write('00_pilot.json', {'photos': [plain, bookdev, port]})

    by_path = {p: int(i) for i, p in json.loads((WORK / 'imports_map.json').read_text()).items()}
    foreign = not_mine()
    imp = [f for f in m['import']['files'] if by_path.get(f) not in foreign]
    write('01_import.json', {'import': {**m['import'], 'files': imp}})
    for k in range(0, len(catalogued), CHUNK):
        write(f'02_tags_{k // CHUNK + 1:02d}.json', {'photos': catalogued[k:k + CHUNK]})
    write('09_tags_imported.json', {'photos': [p for p in imported if by_path.get(p.get('path')) not in foreign]})

    for name, body in files.items():
        n = len(body.get('photos', [])) or len(body.get('import', {}).get('files', []))
        print(f'{name:24s} {n:7,d}  {(out / name).stat().st_size / 1e6:5.1f} MB')
    print(f'import list: {len(m["import"]["files"]) - len(imp)} not-mine files left out')
    print('pilot ids:', plain.get('image_id'), bookdev.get('image_id'), port.get('image_id'))


if __name__ == '__main__':
    main()
