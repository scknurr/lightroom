"""Build the working `images` table from the read-only catalog snapshot."""
import collections
import csv
from pathlib import Path

from config import AUDIT, DRIVE_PREFERENCE, PREVIEWS, SNAPSHOT, ro, work_db


def main():
    cat = ro(SNAPSHOT)
    prev = ro(PREVIEWS / 'previews.db')
    preview = {int(i): (u, d) for i, u, d in prev.execute('SELECT imageId, uuid, digest FROM ImageCacheEntry')}

    state = {}
    for r in csv.DictReader((AUDIT / 'catalog-files.csv').open()):
        state[int(r['file_id'])] = r
    relink = {}
    for r in csv.DictReader((AUDIT / 'drive-hits-missing.csv').open()):
        if r['size_match'] != 'True':
            continue
        fid = int(r['file_id'])
        rank = DRIVE_PREFERENCE.index(r['drive']) if r['drive'] in DRIVE_PREFERENCE else 99
        if fid not in relink or rank < relink[fid][0]:
            relink[fid] = (rank, r['hit_path'])

    hist = dict(cat.execute('SELECT image, COUNT(*) FROM Adobe_libraryImageDevelopHistoryStep GROUP BY image'))
    faces = dict(cat.execute('SELECT image, COUNT(*) FROM AgLibraryFace WHERE regionType=1 OR regionType IS NULL GROUP BY image'))
    exif = {r[0]: r[1:] for r in cat.execute('''
        SELECT e.image, cm.value, l.value, e.focalLength, e.aperture, e.shutterSpeed, e.isoSpeedRating,
               e.hasGPS, e.gpsLatitude, e.gpsLongitude
        FROM AgHarvestedExifMetadata e
        LEFT JOIN AgInternedExifCameraModel cm ON cm.id_local = e.cameraModelRef
        LEFT JOIN AgInternedExifLens l ON l.id_local = e.lensRef''')}

    rows = []
    stats = collections.Counter()
    for (iid, uuid, cap, fmt, pick, rating, w, h, orient, fid, master) in cat.execute('''
            SELECT i.id_local, i.id_global, i.captureTime, i.fileFormat, i.pick, i.rating,
                   i.fileWidth, i.fileHeight, i.orientation, i.rootFile, i.masterImage
            FROM Adobe_images i'''):
        if master is not None:
            continue
        st = state.get(fid, {})
        path, path_state = st.get('path', ''), st.get('state', 'unknown')
        if path_state == 'spelling_equivalent':
            path = str(Path(path).with_name(st['matched_name']))
        elif path_state not in ('found',) and fid in relink:
            path, path_state = relink[fid][1], 'relinked'
        elif path_state != 'found':
            path_state = 'missing'
        pv = preview.get(iid)
        e = exif.get(iid, (None,) * 9)
        rows.append((iid, uuid, fid, cap, (cap or '')[:4], fmt, pick or 0, rating or 0, w, h, orient,
                     path, path_state, pv[0] if pv else None, pv[1] if pv else None,
                     hist.get(iid, 0), faces.get(iid, 0), *e))
        stats[path_state] += 1
        stats['with_preview'] += bool(pv)

    db = work_db()
    db.executescript('''
        DROP TABLE IF EXISTS images;
        CREATE TABLE images (
            image_id INTEGER PRIMARY KEY, uuid TEXT, file_id INTEGER, capture_time TEXT, year TEXT,
            format TEXT, pick INTEGER, rating INTEGER, width REAL, height REAL, orientation TEXT,
            path TEXT, path_state TEXT, preview_uuid TEXT, preview_digest TEXT,
            history_steps INTEGER, faces INTEGER,
            camera TEXT, lens TEXT, focal REAL, aperture REAL, shutter REAL, iso REAL,
            has_gps INTEGER, lat REAL, lon REAL);
        CREATE INDEX images_year ON images(year);
        CREATE INDEX images_time ON images(capture_time);''')
    db.executemany(f'INSERT INTO images VALUES ({",".join("?" * 26)})', rows)
    db.commit()
    print(f'{len(rows):,} master images', dict(stats))


if __name__ == '__main__':
    main()
