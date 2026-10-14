"""Register not-yet-imported photos (import_list_all.json) in the work DB under pseudo ids (>= 900,000,000)
so thumbs/technical/embed/selection can score them. Re-run after inventory.py (which rebuilds `images`)."""
import json
import os
import time

from config import WORK, work_db

BASE = 900_000_000
# EXIF Orientation -> Lightroom catalog orientation code (see thumbs.ORIENT)
EXIF_ORIENT = {1: 'AB', 2: 'BA', 3: 'CD', 4: 'DC', 5: 'AD', 6: 'BC', 7: 'CB', 8: 'DA'}
FORMATS = {'.jpg': 'JPG', '.jpeg': 'JPG', '.png': 'PNG', '.tif': 'TIFF', '.tiff': 'TIFF', '.heic': 'JPG', '.dng': 'DNG',
           '.cr2': 'RAW', '.cr3': 'RAW', '.arw': 'RAW', '.nef': 'RAW', '.mp4': 'VIDEO', '.mov': 'VIDEO'}


def pseudo_id(k):
    return BASE + k


def main():
    files = sorted(json.loads((WORK / 'import_list_all.json').read_text()))
    exif = {}
    for name in ('import_exif.json', 'mac_exif.json', 'import_gps.json', 'import_orient.json'):
        p = WORK / name
        if p.exists():
            try:
                for r in json.loads(p.read_text()):
                    exif.setdefault(r['SourceFile'], {}).update(r)
            except json.JSONDecodeError:
                pass
    db = work_db()
    db.execute(f'DELETE FROM images WHERE image_id >= {BASE}')
    rows = []
    for k, path in enumerate(files):
        e = exif.get(path, {})
        dt = str(e.get('DateTimeOriginal') or e.get('CreateDate') or '')
        cap = (dt[:10].replace(':', '-') + 'T' + dt[11:19]) if len(dt) >= 19 and dt[:4].isdigit() else \
            time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(os.path.getmtime(path))) if os.path.exists(path) else ''
        fmt = FORMATS.get(os.path.splitext(path)[1].lower(), 'JPG')
        rows.append((pseudo_id(k), None, None, cap, cap[:4], fmt, 0, 0, None, None, EXIF_ORIENT.get(e.get('Orientation'), 'AB'), path, 'uncataloged', None, None,
                     0, 0, str(e.get('Model') or '') or None, None, None, None, None, None, 0, None, None, None))
    db.executemany(f'INSERT INTO images VALUES ({",".join("?" * 27)})', rows)
    db.commit()
    (WORK / 'imports_map.json').write_text(json.dumps({str(pseudo_id(k)): p for k, p in enumerate(files)}))
    print(f'registered {len(rows):,} uncataloged photos as {BASE}..{BASE + len(rows) - 1}')


if __name__ == '__main__':
    main()
