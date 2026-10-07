"""Inventory media on the photo drives that never made it into the Lightroom catalog.

Each media file is classified as:
  cataloged   - the catalog references this exact path
  companion   - same folder + base name as a cataloged file (RAW+JPEG twin / registered sidecar)
  copy        - same file name and size as a cataloged file elsewhere (a duplicate copy)
  uncataloged - none of the above: a candidate for import
Uncataloged files are de-duplicated among themselves by name + size.
Read-only. Writes WORK/uncataloged.csv and WORK/uncataloged_summary.json.
"""
import collections
import csv
import json
import os
import re
import unicodedata

from config import WORK, work_db

ROOTS = ['/Volumes/Y DRIVE', '/Volumes/G DRIVE', '/Volumes/H DRIVE']
SKIP_DIRS = {'TOTAL-v13-3 Smart Previews.lrdata', 'TOTAL-v13-3 Previews.lrdata', 'LR-RESCUE.noindex'}
MEDIA = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.psd', '.dng', '.cr2', '.cr3', '.nef', '.arw', '.raf',
         '.orf', '.rw2', '.heic', '.heif', '.mov', '.mp4', '.m4v', '.avi', '.mts', '.3gp', '.webp', '.gif'}
VIDEO = {'.mov', '.mp4', '.m4v', '.avi', '.mts', '.3gp'}
NAME_DATE = re.compile(r'(?:PXL|IMG|VID|MVIMG|Screenshot|PANO|BURST)[_-](20\d{2})(\d{2})(\d{2})')


def norm(p):
    return unicodedata.normalize('NFC', p).casefold()


def year_of(path, mtime):
    m = NAME_DATE.search(os.path.basename(path))
    if m:
        return m.group(1), 'filename'
    for seg in path.split('/')[3:]:
        m = re.match(r'^((?:19|20)\d{2})(?:[-_ ]|$)', seg)
        if m:
            return m.group(1), 'folder'
    import time
    return time.strftime('%Y', time.localtime(mtime)), 'mtime'


def main():
    db = work_db()
    cataloged, stems, name_size = set(), set(), set()
    for path, size in db.execute('SELECT path, file_size FROM images'):
        n = norm(path)
        cataloged.add(n)
        stems.add(os.path.splitext(n)[0])
        name_size.add((os.path.basename(n), size))
    counts = collections.Counter()
    by_folder = collections.Counter()
    by_year = collections.Counter()
    seen_uncat = set()
    out = open(WORK / 'uncataloged.csv', 'w', newline='')
    w = csv.writer(out)
    w.writerow(['path', 'size', 'kind', 'year', 'year_source', 'top_folder'])
    for root in ROOTS:
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in SKIP_DIRS
                           and not d.endswith('.fcpbundle') and not d.endswith('.fcpcache')]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in MEDIA or f.startswith('._'):
                    continue
                full = os.path.join(dirpath, f)
                n = norm(full)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if n in cataloged:
                    kind = 'cataloged'
                elif os.path.splitext(n)[0] in stems:
                    kind = 'companion'
                elif (norm(f), st.st_size) in name_size:
                    kind = 'copy'
                else:
                    key = (norm(f), st.st_size)
                    kind = 'uncataloged_dup' if key in seen_uncat else 'uncataloged'
                    seen_uncat.add(key)
                top = '/'.join(full.split('/')[2:4])
                counts[(root, kind)] += 1
                if kind == 'uncataloged':
                    y, src = year_of(full, st.st_mtime)
                    by_folder[top] += 1
                    by_year[(y, 'video' if ext in VIDEO else 'photo')] += 1
                    w.writerow([full, st.st_size, 'video' if ext in VIDEO else 'photo', y, src, top])
        print(root, {k: v for (r, k), v in counts.items() if r == root}, flush=True)
    out.close()
    summary = {
        'by_root_kind': {f'{r} | {k}': v for (r, k), v in sorted(counts.items())},
        'uncataloged_by_top_folder': dict(by_folder.most_common()),
        'uncataloged_by_year': {f'{y} {t}': n for (y, t), n in sorted(by_year.items())},
    }
    (WORK / 'uncataloged_summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary['uncataloged_by_top_folder'], indent=1)[:3000])


if __name__ == '__main__':
    main()
