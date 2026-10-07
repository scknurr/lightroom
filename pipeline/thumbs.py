"""Extract ~1440px JPEG thumbnails: Lightroom preview cache first, else the original (embedded RAW JPEG)."""
import argparse
import io
import re
import struct
import subprocess
import sys
from multiprocessing import Pool

from PIL import Image, ImageOps

from config import PREVIEWS, THUMB_LONG, thumb_path, work_db

Image.MAX_IMAGE_PIXELS = None
RASTER = {'JPG', 'PNG', 'TIFF', 'PSD'}
RAW_TAGS = ['-JpgFromRaw', '-PreviewImage', '-OtherImage']
ROTATE = {'3': 180, '6': 270, '8': 90}


def from_lrprev(uuid, digest):
    path = PREVIEWS / uuid[0] / uuid[:4] / f'{uuid}-{digest}.lrprev'
    chunks = {}
    with open(path, 'rb') as f:
        while True:
            head = f.read(32)
            if len(head) < 32 or head[:4] != b'AgHg':
                break
            hlen = struct.unpack('>H', head[4:6])[0]
            dlen, plen = struct.unpack('>QQ', head[8:24])
            name = head[24:hlen].split(b'\0')[0].decode()
            start = f.tell() + hlen - 32
            chunks[name] = (start, dlen)
            f.seek(start + dlen + plen)
        f.seek(chunks['header'][0])
        header = f.read(chunks['header'][1]).decode('utf-8', 'replace')
        dims = [max(int(h), int(w)) for h, w in re.findall(r'height = (\d+),\s*width = (\d+)', header)]
        levels = [(i + 1, d) for i, d in enumerate(dims) if f'level_{i + 1}' in chunks]
        if not levels:
            raise ValueError('no levels')
        fitting = [lv for lv in levels if lv[1] <= THUMB_LONG * 1.12]
        level = fitting[-1] if fitting and fitting[-1][1] >= 900 else min(
            (lv for lv in levels if lv[1] >= 900), key=lambda lv: lv[1], default=levels[-1])
        start, dlen = chunks[f'level_{level[0]}']
        f.seek(start)
        data = f.read(dlen)
    im = Image.open(io.BytesIO(data))
    if max(im.size) > THUMB_LONG * 1.12:
        im = im.convert('RGB')
        im.thumbnail((THUMB_LONG, THUMB_LONG), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=88)
        data = buf.getvalue()
    return data, im.size, 'preview'


def from_original(path, fmt):
    if fmt in RASTER:
        im = ImageOps.exif_transpose(Image.open(path))
        src = 'original'
    else:
        data = b''
        for tag in RAW_TAGS:
            data = subprocess.run(['exiftool', '-b', tag, path], capture_output=True, timeout=60).stdout
            if data[:2] == b'\xff\xd8' and len(data) > 50_000:
                break
        if data[:2] != b'\xff\xd8':
            raise ValueError('no embedded jpeg')
        im = Image.open(io.BytesIO(data))
        orient = subprocess.run(['exiftool', '-n', '-s3', '-Orientation', path],
                                capture_output=True, text=True, timeout=30).stdout.strip()
        if orient in ROTATE:
            im = im.rotate(ROTATE[orient], expand=True)
        src = 'raw_embedded'
    im = im.convert('RGB')
    im.thumbnail((THUMB_LONG, THUMB_LONG), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=88)
    return buf.getvalue(), im.size, src


def work(row):
    iid, fmt, path, state, puuid, pdig = row
    out = thumb_path(iid)
    try:
        if fmt == 'VIDEO':
            return iid, 'skip_video', None, None, ''
        result = None
        if puuid:
            try:
                result = from_lrprev(puuid, pdig)
            except Exception:
                pass
        if result is None:
            if state == 'missing':
                return iid, 'fail', None, None, 'original missing'
            result = from_original(path, fmt)
        data, (w, h), src = result
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return iid, src, w, h, ''
    except Exception as e:
        return iid, 'fail', None, None, str(e)[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', action='append', help='Limit to capture year(s); default all')
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args()
    db = work_db()
    db.execute('CREATE TABLE IF NOT EXISTS thumbs (image_id INTEGER PRIMARY KEY, source TEXT, w INTEGER, h INTEGER, error TEXT)')
    q = '''SELECT image_id, format, path, path_state, preview_uuid, preview_digest FROM images
           WHERE image_id NOT IN (SELECT image_id FROM thumbs WHERE source != 'fail')'''
    params = []
    if args.year:
        q += f' AND year IN ({",".join("?" * len(args.year))})'
        params = args.year
    todo = db.execute(q, params).fetchall()
    print(f'{len(todo):,} to extract', flush=True)
    done = 0
    with Pool(args.workers) as pool:
        for res in pool.imap_unordered(work, todo, chunksize=8):
            db.execute('INSERT OR REPLACE INTO thumbs VALUES (?,?,?,?,?)', res)
            done += 1
            if done % 100 == 0:
                db.commit()
            if done % 2000 == 0:
                print(f'{done:,}/{len(todo):,}', flush=True)
    db.commit()
    for src, n in db.execute('SELECT source, COUNT(*) FROM thumbs GROUP BY source'):
        print(src, n)


if __name__ == '__main__':
    sys.exit(main())
