"""Read-only Lightroom inventory. Writes reports only under the chosen output folder."""
import argparse
import collections
import csv
import json
import os
from pathlib import Path
import sqlite3
import time
import unicodedata

MEDIA = {'.jpg','.jpeg','.png','.tif','.tiff','.psd','.psb','.dng','.cr2','.cr3',
         '.nef','.arw','.raf','.orf','.rw2','.heic','.heif','.avif','.mov','.mp4',
         '.m4v','.avi','.mts','.m2ts','.3gp','.webp','.gif'}


def norm(s):
    return unicodedata.normalize('NFC', s).casefold()


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('catalog', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--reuse-snapshot', action='store_true', help='Resume analysis of a previously created, complete snapshot.')
    args = ap.parse_args()
    out = args.output.resolve()
    source = args.catalog.resolve()
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out / 'catalog-audit-snapshot.sqlite'
    if snapshot.exists() and not args.reuse_snapshot:
        raise SystemExit('Snapshot already exists; choose a new output directory.')
    if not args.reuse_snapshot:
        print('Creating transaction-consistent SQLite audit snapshot (not a complete Lightroom backup).', flush=True)
        src = sqlite3.connect(source.as_uri() + '?mode=ro', timeout=5)
        src.execute('PRAGMA query_only=ON')
        dst = sqlite3.connect(snapshot)
        started = last = time.monotonic()
        def progress(status, remaining, total):
            nonlocal last
            now = time.monotonic()
            if now - started > 180:
                raise TimeoutError('Snapshot exceeded three minutes.')
            if now - last > 10:
                print(f'Snapshot: {total-remaining:,}/{total:,} pages', flush=True)
                last = now
        try:
            src.backup(dst, pages=2048, progress=progress, sleep=0.05)
        finally:
            dst.close()
            src.close()
    if Path(str(snapshot)+'-wal').exists():
        raise SystemExit('Unexpected snapshot WAL; refusing immutable read.')
    # Only this private, closed backup is immutable. Never use immutable on the live catalog.
    c = sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1')
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    def rows(q):
        return [dict(r) for r in c.execute(q)]
    def scalar(q):
        return c.execute(q).fetchone()[0]
    summary = {
        'catalog': str(source), 'audit_snapshot': str(snapshot),
        'snapshot_is_complete_lightroom_backup': False,
        'image_records': scalar('SELECT COUNT(*) FROM Adobe_images'),
        'file_records': scalar('SELECT COUNT(*) FROM AgLibraryFile'),
        'virtual_copies': scalar('SELECT COUNT(*) FROM Adobe_images WHERE masterImage IS NOT NULL'),
        'tagged_image_records': scalar('SELECT COUNT(DISTINCT image) FROM AgLibraryKeywordImage'),
        'keyword_records': scalar('SELECT COUNT(*) FROM AgLibraryKeyword'),
        'rated_image_records': scalar('SELECT COUNT(*) FROM Adobe_images WHERE rating > 0'),
        'develop_adjusted_records': None,
        'formats': rows('SELECT fileFormat,COUNT(*) count FROM Adobe_images GROUP BY 1 ORDER BY 2 DESC'),
        'capture_years': rows('SELECT substr(captureTime,1,4) year,COUNT(*) count FROM Adobe_images GROUP BY 1 ORDER BY 1'),
    }
    summary['untagged_image_records'] = summary['image_records'] - summary['tagged_image_records']
    keywords = rows('''SELECT k.id_local,k.name,k.parent,k.keywordType,COUNT(ki.image) image_count
        FROM AgLibraryKeyword k LEFT JOIN AgLibraryKeywordImage ki ON ki.tag=k.id_local
        GROUP BY k.id_local ORDER BY image_count DESC''')
    write_csv(out/'keywords.csv', keywords, ['id_local','name','parent','keywordType','image_count'])
    folders = rows('''SELECT f.id_local,r.absolutePath root,f.pathFromRoot relative_path
        FROM AgLibraryFolder f JOIN AgLibraryRootFolder r ON r.id_local=f.rootFolder''')
    files = rows('''SELECT af.id_local file_id,af.folder folder_id,af.idx_filename filename,af.extension,af.sidecarExtensions
        FROM AgLibraryFile af''')
    by_folder = collections.defaultdict(list)
    for f in files:
        by_folder[f['folder_id']].append(f)
    root_counts = collections.defaultdict(collections.Counter)
    folder_report, missing, extras, companions, errors = [], [], [], [], []
    states = collections.Counter()
    with (out/'catalog-files.csv').open('w', newline='', encoding='utf-8') as cf:
        fields = ['file_id','path','state','matched_name']
        cw = csv.DictWriter(cf, fields)
        cw.writeheader()
        for index, folder in enumerate(folders):
            p = Path(folder['root']) / folder['relative_path']
            listed, lookup = {}, collections.defaultdict(list)
            state, error = 'readable', ''
            try:
                with os.scandir(p) as it:
                    for e in it:
                        if e.is_file(follow_symlinks=False):
                            listed[e.name] = e.name
                            lookup[norm(e.name)].append(e.name)
            except FileNotFoundError:
                state = 'folder_absent'
            except NotADirectoryError:
                state = 'not_directory'
            except OSError as e:
                state, error = 'unreadable', str(e)
                errors.append({'path':str(p),'error':error})
            fr = {'folder_id':folder['id_local'],'path':str(p),'state':state,
                  'catalog_files':len(by_folder[folder['id_local']]),'found':0,'absent':0,'unreadable':0}
            expected, sidecars = set(), set()
            for f in by_folder[folder['id_local']]:
                filename = f['filename']
                expected.add(norm(filename))
                for ext in (f['sidecarExtensions'] or '').split(','):
                    if ext:
                        sidecars.add(norm(str(Path(filename).with_suffix('.'+ext))))
                match = ''
                if state == 'unreadable':
                    fs = 'unreadable'
                elif state != 'readable':
                    fs = state
                elif filename in listed:
                    fs, match = 'found', filename
                elif len(lookup[norm(filename)]) == 1:
                    # Report separately: equivalent spelling does not prove volume case semantics.
                    fs, match = 'spelling_equivalent', lookup[norm(filename)][0]
                else:
                    fs = 'file_absent'
                row = {'file_id':f['file_id'],'path':str(p/filename),'state':fs,'matched_name':match}
                cw.writerow(row)
                states[fs] += 1
                root_counts[folder['root']][fs] += 1
                if fs in ('found','spelling_equivalent'):
                    fr['found'] += 1
                elif fs == 'unreadable':
                    fr['unreadable'] += 1
                else:
                    fr['absent'] += 1
                    missing.append(row)
            if state == 'readable':
                for name in listed:
                    if Path(name).suffix.lower() in MEDIA and norm(name) not in expected:
                        row = {'path':str(p/name),'folder_id':folder['id_local']}
                        (companions if norm(name) in sidecars else extras).append(row)
            folder_report.append(fr)
            if index % 250 == 0:
                print(f'Folders checked: {index+1:,}/{len(folders):,}; files: {sum(states.values()):,}; absent paths: {len(missing):,}', flush=True)
    assert sum(states.values()) == summary['file_records'], 'Every catalog file must have a checked folder.'
    write_csv(out/'folders.csv', folder_report, ['folder_id','path','state','catalog_files','found','absent','unreadable'])
    write_csv(out/'missing-original-paths.csv', missing, ['file_id','path','state','matched_name'])
    write_csv(out/'extra-media-in-catalog-folders.csv', extras, ['path','folder_id'])
    write_csv(out/'catalog-media-companions.csv', companions, ['path','folder_id'])
    write_csv(out/'read-errors.csv', errors, ['path','error'])
    candidates = rows('''SELECT af.idx_filename filename,i.captureTime,COUNT(DISTINCT af.id_local) distinct_files
        FROM Adobe_images i JOIN AgLibraryFile af ON af.id_local=i.rootFile
        WHERE i.masterImage IS NULL AND i.captureTime IS NOT NULL AND i.captureTime != ''
        GROUP BY lower(af.idx_filename),i.captureTime HAVING COUNT(DISTINCT af.id_local)>1
        ORDER BY distinct_files DESC''')
    write_csv(out/'duplicate-candidates-unverified.csv', candidates, ['filename','captureTime','distinct_files'])
    summary.update(file_states=dict(states), roots=dict(root_counts),
                   extra_media_in_catalog_folders=len(extras), catalog_media_companions=len(companions), read_errors=errors,
                   duplicate_candidate_groups_unverified=len(candidates),
                   missing_original_paths=len(missing),
                   limits=['Snapshot excludes lrcat-data, previews and original photos; it is an analysis artifact, not a full recovery backup.',
                           'Presence checks cover paths in this catalog only. Extra media includes only immediate catalog folders.',
                           'Absence means unavailable at the catalog path, not proof of deletion.',
                           'Duplicate candidates use filename and capture time, not content hashes.',
                           'Develop-adjustment cache flags are unpopulated; edit counts are not inferred from them.',
                           'No catalog, keyword, collection, original photo or sidecar was edited.'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    c.close()
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
