"""Build a thumbnail cache for every master image in the catalog (roadmap step 1).

Read-only with respect to Lightroom: reads the audit snapshot, the catalog's
Previews.lrdata, and original files; never writes to any of them.

Source order per image:
  1. Lightroom preview pyramid (.lrprev) - smallest level whose long edge >= --size
  2. Original file:
       RAW (CR2/NEF/ARW/DNG/...) -> largest JPEG embedded in the TIFF structure
       JPG/PNG/TIFF/PSD/HEIC     -> decoded directly (JPEG uses draft-mode decode)
  3. A small preview (< --size) if nothing better was readable

All sources are rotated with the *catalog* orientation (AB/BC/CD/DA), which is the
orientation Lightroom displays, so thumbnails come out upright.

Outputs (under <out_dir>):
  thumbs/<image_id // 1000>/<image_id>.jpg
  thumbs.sqlite   - manifest: one row per image (source, sizes, status, error)
  thumbs-summary.json

Resumable: rerun the same command and finished images are skipped.

Usage:
  .venv/bin/python build_thumbs.py 2026-10-05 \
      --previews "/Volumes/Y DRIVE/TOTAL/TOTAL-v13-3 Previews.lrdata"
"""
import argparse
import collections
import csv
import io
import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import struct
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None

try:  # optional HEIC support
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

RAW_EXT = {'cr2', 'cr3', 'nef', 'nrw', 'arw', 'srf', 'sr2', 'dng', 'orf', 'rw2', 'raf', 'pef', 'srw', 'raw'}
PIL_EXT = {'jpg', 'jpeg', 'png', 'tif', 'tiff', 'psd', 'heic', 'heif', 'webp', 'gif', 'bmp'}
VIDEO_EXT = {'mov', 'mp4', 'm4v', 'avi', 'mts', 'm2ts', '3gp'}

# Lightroom orientation codes -> PIL transpose (verified against real previews)
ORIENT = {
    'BC': Image.Transpose.ROTATE_270,   # 90 deg clockwise
    'CD': Image.Transpose.ROTATE_180,
    'DA': Image.Transpose.ROTATE_90,    # 90 deg counter-clockwise
}
# Mirrored variants (rare): first letter pair reversed
ORIENT_MIRROR = {
    'BA': Image.Transpose.FLIP_LEFT_RIGHT,
    'CB': Image.Transpose.TRANSVERSE,
    'DC': Image.Transpose.FLIP_TOP_BOTTOM,
    'AD': Image.Transpose.TRANSPOSE,
}


# ---------------------------------------------------------------- lrprev

def lrprev_levels(path):
    """Return [(long_edge_guess_index, name, offset, length)] for JPEG levels in an .lrprev.

    Each section: 'AgHg' | u16 header_len | u8 version | u8 kind | u64 data_len |
    u64 pad_len | name (NUL padded) ... then data, then padding. Big-endian.
    """
    out = []
    with open(path, 'rb') as f:
        off = 0
        while True:
            f.seek(off)
            h = f.read(24)
            if len(h) < 24 or h[:4] != b'AgHg':
                break
            hl = struct.unpack('>H', h[4:6])[0]
            dl, pl = struct.unpack('>QQ', h[8:24])
            name = f.read(hl - 24).split(b'\0')[0].decode('ascii', 'replace')
            if name.startswith('level_'):
                out.append((name, off + hl, dl))
            off += hl + dl + pl
    return out


def jpeg_dims(buf):
    """Width/height from a JPEG's SOF marker without decoding."""
    i = 2
    n = len(buf)
    while i + 9 < n:
        if buf[i] != 0xFF:
            i += 1
            continue
        m = buf[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        L = struct.unpack('>H', buf[i + 2:i + 4])[0]
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack('>HH', buf[i + 5:i + 9])
            return w, h
        i += 2 + L
    return None


SPLIT_LEVELS = [90 * 2 ** k for k in range(8)]  # 90 .. 11520


def best_split_preview(base, size):
    """Newer Lightroom layout: one plain JPEG per level, named '<uuid>-<digest>_<long edge>'."""
    order = [s for s in SPLIT_LEVELS if s >= size] + [s for s in reversed(SPLIT_LEVELS) if s < size]
    for s in order:
        p = f'{base}_{s}'
        try:
            with open(p, 'rb') as f:
                return f.read(), s
        except FileNotFoundError:
            continue
    return None, 0


def best_preview(base, size):
    """Pick the smallest preview level with long edge >= size, else the largest.

    `base` is '<previews>/<u>/<uuuu>/<uuid>-<digest>' (no extension).
    Returns (jpeg_bytes, long_edge) or (None, 0).
    """
    path = base + '.lrprev'
    if not os.path.exists(path):
        return best_split_preview(base, size)
    levels = lrprev_levels(path)
    if not levels:
        return None, 0
    with open(path, 'rb') as f:
        cands = []
        for name, off, ln in levels:
            f.seek(off)
            head = f.read(min(ln, 65536))
            d = jpeg_dims(head)
            if d:
                cands.append((max(d), off, ln))
        if not cands:
            return None, 0
        cands.sort()
        pick = next((c for c in cands if c[0] >= size), cands[-1])
        f.seek(pick[1])
        return f.read(pick[2]), pick[0]


# ---------------------------------------------------------------- RAW embedded JPEG

def raw_embedded_jpeg(path):
    """Largest JPEG embedded in a TIFF-structured RAW (CR2, NEF, ARW, DNG, PEF, ...).

    Walks IFD chains, SubIFDs (0x14A) and the EXIF IFD (0x8769) looking for
    JPEGInterchangeFormat (0x201/0x202) and strip (0x111/0x117) pairs that point
    at JPEG data. Reads only the bytes it needs.
    """
    with open(path, 'rb') as f:
        hdr = f.read(8)
        if hdr[:2] == b'II':
            e = '<'
        elif hdr[:2] == b'MM':
            e = '>'
        else:
            return None
        if struct.unpack(e + 'H', hdr[2:4])[0] not in (42, 0x4F52, 0x5352):  # TIFF, ORF variants
            return None
        fsize = os.fstat(f.fileno()).st_size
        TYPES = {1: 1, 3: 2, 4: 4, 7: 1, 13: 4, 16: 8}

        def values(typ, count, raw):
            sz = TYPES.get(typ)
            if not sz:
                return []
            if sz * count > 4:
                (ptr,) = struct.unpack(e + 'I', raw)
                f.seek(ptr)
                data = f.read(sz * count)
            else:
                data = raw[:sz * count]
            fmt = {1: 'B', 2: 'H', 4: 'I', 8: 'Q'}[sz]
            try:
                return list(struct.unpack(e + fmt * count, data))
            except struct.error:
                return []

        cands = []
        seen = set()
        stack = [struct.unpack(e + 'I', hdr[4:8])[0]]
        while stack:
            ifd = stack.pop()
            hops = 0
            while ifd and ifd not in seen and ifd < fsize and hops < 16:
                seen.add(ifd)
                hops += 1
                f.seek(ifd)
                nb = f.read(2)
                if len(nb) < 2:
                    break
                (n,) = struct.unpack(e + 'H', nb)
                if n > 1000:
                    break
                ents = f.read(12 * n)
                nxt = f.read(4)
                tags = {}
                for k in range(n):
                    tag, typ, cnt = struct.unpack(e + 'HHI', ents[12 * k:12 * k + 8])
                    tags[tag] = (typ, cnt, ents[12 * k + 8:12 * k + 12])
                def get(tag):
                    return values(*tags[tag]) if tag in tags else []
                jo, jl = get(0x201), get(0x202)
                if jo and jl:
                    cands.append((jl[0], jo[0]))
                so, sl = get(0x111), get(0x117)
                if len(so) == 1 and len(sl) == 1:
                    cands.append((sl[0], so[0]))
                for t in (0x14A, 0x8769):
                    stack.extend(get(t))
                ifd = struct.unpack(e + 'I', nxt)[0] if len(nxt) == 4 else 0

        best = None
        for ln, off in sorted(set(cands), reverse=True):
            if ln < 1024 or off + ln > fsize:
                continue
            f.seek(off)
            if f.read(3) != b'\xff\xd8\xff':
                continue
            f.seek(off)
            best = f.read(ln)
            # Skip lossless-JPEG raw data (SOF3) that some formats store as a "JPEG"
            d = jpeg_dims(best[:65536])
            if d is None or b'\xff\xc3' in best[:65536]:
                best = None
                continue
            break
        return best


EXIFTOOL = shutil.which('exiftool')


def exiftool_preview(path):
    """Fallback for RAW layouts the TIFF walker doesn't handle (CR3, RAF, odd firmware)."""
    if not EXIFTOOL:
        return None
    best = None
    for tag in ('-JpgFromRaw', '-PreviewImage', '-OtherImage'):
        try:
            r = subprocess.run([EXIFTOOL, '-b', tag, path], capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        if r.stdout[:3] == b'\xff\xd8\xff' and (best is None or len(r.stdout) > len(best)):
            best = r.stdout
    return best


# ---------------------------------------------------------------- decoding

def open_jpeg_bytes(buf, size):
    im = Image.open(io.BytesIO(buf))
    im.draft('RGB', (size, size))
    return im


def finish(im, orientation, size, dest):
    if im.mode not in ('RGB', 'L'):
        if im.mode in ('RGBA', 'LA', 'P', 'PA'):
            im = im.convert('RGBA')
            bg = Image.new('RGB', im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert('RGB')
    src = im.size
    im.thumbnail((size, size), Image.Resampling.LANCZOS)
    t = ORIENT.get(orientation) or ORIENT_MIRROR.get(orientation)
    if t is not None:
        im = im.transpose(t)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    im.save(tmp, 'JPEG', quality=85, optimize=True)
    os.replace(tmp, dest)
    return src, im.size


def process(job):
    """Worker: job is a dict. Returns a manifest row."""
    size, thumbs = job['size'], Path(job['thumbs'])
    iid = job['image_id']
    dest = thumbs / str(iid // 1000) / f'{iid}.jpg'
    row = {'image_id': iid, 'source': None, 'src_w': None, 'src_h': None,
           'w': None, 'h': None, 'status': 'failed', 'error': ''}
    errors = []
    small = None

    # 1. Lightroom preview
    if job.get('preview'):
        try:
            buf, long_edge = best_preview(job['preview'], size)
            if buf is not None:
                if long_edge >= size * 0.75:
                    s, d = finish(open_jpeg_bytes(buf, size), job['orientation'], size, dest)
                    row.update(source='preview', src_w=s[0], src_h=s[1], w=d[0], h=d[1], status='ok')
                    return row
                small = buf
        except Exception as ex:
            errors.append(f'preview: {type(ex).__name__}: {ex}')

    # 2. Original
    ext, path = job['ext'], job['path']
    if job['present'] and path:
        try:
            if ext in RAW_EXT:
                try:
                    buf = raw_embedded_jpeg(path)
                except (OSError, struct.error, ValueError) as ex:
                    if isinstance(ex, FileNotFoundError):
                        raise
                    buf = None
                if buf is None:
                    buf = exiftool_preview(path)
                if buf is None:
                    raise ValueError('no embedded JPEG found')
                s, d = finish(open_jpeg_bytes(buf, size), job['orientation'], size, dest)
                row.update(source='raw_embedded', src_w=s[0], src_h=s[1], w=d[0], h=d[1], status='ok')
                return row
            if ext in PIL_EXT:
                im = Image.open(path)
                if im.format == 'JPEG':
                    im.draft('RGB', (size, size))
                if getattr(im, 'n_frames', 1) > 1:
                    im.seek(0)
                s, d = finish(im, job['orientation'], size, dest)
                row.update(source='original', src_w=s[0], src_h=s[1], w=d[0], h=d[1], status='ok')
                return row
            if ext in VIDEO_EXT:
                errors.append('video without preview')
            else:
                errors.append(f'unsupported extension .{ext}')
        except Exception as ex:
            errors.append(f'original: {type(ex).__name__}: {ex}')
    elif not job['present']:
        errors.append('original not on disk')

    # 3. Small preview as a last resort
    if small is not None:
        try:
            s, d = finish(open_jpeg_bytes(small, size), job['orientation'], size, dest)
            row.update(source='preview_small', src_w=s[0], src_h=s[1], w=d[0], h=d[1], status='ok')
            row['error'] = '; '.join(errors)
            return row
        except Exception as ex:
            errors.append(f'preview_small: {type(ex).__name__}: {ex}')

    row['status'] = 'skipped' if errors and all(
        e in ('video without preview', 'original not on disk') or e.startswith('unsupported') for e in errors) else 'failed'
    row['error'] = '; '.join(errors)[:500]
    return row


# ---------------------------------------------------------------- driver

def load_jobs(out, previews, size, thumbs):
    snap = out / 'catalog-audit-snapshot.sqlite'
    if Path(str(snap) + '-wal').exists():
        raise SystemExit('Unexpected snapshot WAL; refusing immutable read.')
    c = sqlite3.connect(snap.as_uri() + '?mode=ro&immutable=1', uri=True)
    c.execute('PRAGMA query_only=ON')

    preview_by_image = {}
    if previews:
        pdb = previews / 'previews.db'
        p = sqlite3.connect(pdb.as_uri() + '?mode=ro', uri=True, timeout=10)
        p.execute('PRAGMA query_only=ON')
        for iid, uuid, digest in p.execute('SELECT imageId, uuid, digest FROM ImageCacheEntry'):
            preview_by_image[int(iid)] = str(previews / uuid[0] / uuid[:4] / f'{uuid}-{digest}')
        p.close()

    present = {}
    with (out / 'catalog-files.csv').open(encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r['state'] in ('found', 'spelling_equivalent'):
                name = r['matched_name'] or os.path.basename(r['path'])
                present[int(r['file_id'])] = str(Path(r['path']).with_name(name))

    q = '''SELECT i.id_local, i.orientation, af.id_local, lower(af.extension)
           FROM Adobe_images i JOIN AgLibraryFile af ON af.id_local = i.rootFile
           WHERE i.masterImage IS NULL'''
    jobs = []
    for iid, orient, fid, ext in c.execute(q):
        path = present.get(fid)
        jobs.append({'image_id': iid, 'orientation': orient or 'AB', 'ext': ext or '',
                     'path': path, 'present': path is not None,
                     'preview': preview_by_image.get(iid), 'size': size, 'thumbs': str(thumbs)})
    c.close()
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('output', type=Path, help='Audit output folder (e.g. 2026-10-05).')
    ap.add_argument('--previews', type=Path, help="The catalog's 'Previews.lrdata' folder.")
    ap.add_argument('--size', type=int, default=512, help='Long edge in pixels (default 512).')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--limit', type=int, help='Only process this many pending images (for testing).')
    ap.add_argument('--dest', type=Path, help='Where to write thumbs/ and thumbs.sqlite (default: the output folder).')
    ap.add_argument('--retry-failed', action='store_true', help='Also retry images that failed before.')
    args = ap.parse_args()

    out = args.output.resolve()
    dest = (args.dest or args.output).resolve()
    thumbs = dest / 'thumbs'
    thumbs.mkdir(parents=True, exist_ok=True)
    if args.previews and not (args.previews / 'previews.db').exists():
        raise SystemExit(f'No previews.db in {args.previews}')

    print('Loading catalog and preview index...', flush=True)
    jobs = load_jobs(out, args.previews.resolve() if args.previews else None, args.size, thumbs)

    man = sqlite3.connect(dest / 'thumbs.sqlite')
    man.execute('''CREATE TABLE IF NOT EXISTS thumbs (
        image_id INTEGER PRIMARY KEY, source TEXT, src_w INT, src_h INT, w INT, h INT,
        status TEXT, error TEXT, size INT, updated REAL)''')
    done = {r[0]: r[1] for r in man.execute('SELECT image_id, status FROM thumbs WHERE size = ?', (args.size,))}
    skip = {'ok', 'skipped'} | (set() if args.retry_failed else {'failed'})
    pending = [j for j in jobs if done.get(j['image_id']) not in skip]
    already = len(jobs) - len(pending)
    if args.limit:
        pending = pending[:args.limit]
    print(f'{len(jobs):,} master images; {already:,} already done; '
          f'{len(pending):,} to process with {args.workers} workers.', flush=True)

    counts = collections.Counter()
    started = last = time.monotonic()
    batch = []

    def flush():
        man.executemany('INSERT OR REPLACE INTO thumbs VALUES (?,?,?,?,?,?,?,?,?,?)', batch)
        man.commit()
        batch.clear()

    try:
        with mp.Pool(args.workers) as pool:
            for i, row in enumerate(pool.imap_unordered(process, pending, chunksize=16), 1):
                counts[(row['status'], row['source'])] += 1
                batch.append((row['image_id'], row['source'], row['src_w'], row['src_h'], row['w'], row['h'],
                              row['status'], row['error'], args.size, time.time()))
                if len(batch) >= 500:
                    flush()
                now = time.monotonic()
                if now - last > 15 or i == len(pending):
                    rate = i / max(now - started, 1e-6)
                    eta = (len(pending) - i) / rate if rate else 0
                    print(f'{i:,}/{len(pending):,}  {rate:.0f}/s  eta {eta / 60:.0f} min  '
                          + ', '.join(f'{s}/{src or "-"}={n:,}' for (s, src), n in sorted(counts.items(), key=str)),
                          flush=True)
                    last = now
    except KeyboardInterrupt:
        print('\nInterrupted; progress saved. Rerun the same command to resume.', flush=True)
    finally:
        if batch:
            flush()

    summary = {
        'size': args.size,
        'master_images': len(jobs),
        'by_status_source': [
            {'status': s, 'source': src, 'count': n}
            for s, src, n in man.execute('SELECT status, source, COUNT(*) FROM thumbs WHERE size=? GROUP BY 1,2 ORDER BY 3 DESC', (args.size,))],
        'top_errors': [
            {'error': e, 'count': n}
            for e, n in collections.Counter(
                '; '.join(part.split(': [Errno')[0].split(':', 2)[0] + ':' + (part.split(':', 2)[1] if part.count(':') else '')
                          for part in (err or '').split('; '))
                for (err,) in man.execute("SELECT error FROM thumbs WHERE size=? AND status != 'ok'", (args.size,))
            ).most_common(15)],
    }
    man.close()
    (dest / 'thumbs-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
