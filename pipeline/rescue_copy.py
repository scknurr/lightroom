"""Copy-only backup of files that exist only on a degraded source drive.

Reads each listed relative path once from SRC and writes it under DEST, preserving the relative path and
timestamps. Never modifies or deletes anything on SRC. Skips files already copied with matching size, and
silently skips list entries that don't exist (optional sidecars). Resumable.
"""
import argparse
import os
import shutil
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dest')
    ap.add_argument('list_file')
    args = ap.parse_args()
    src, dest = Path(args.src), Path(args.dest)
    rels = [l.rstrip('\n') for l in open(args.list_file) if l.strip()]
    log = open(dest / 'copy.log', 'a')
    done = skipped = missing = failed = 0
    nbytes = 0
    t0 = time.time()
    for i, rel in enumerate(rels, 1):
        s, d = src / rel, dest / rel
        try:
            st = os.stat(s)
        except FileNotFoundError:
            missing += 1
            continue
        if d.exists() and d.stat().st_size == st.st_size:
            skipped += 1
            continue
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            tmp = d.with_name(d.name + '.partial')
            shutil.copyfile(s, tmp)
            shutil.copystat(s, tmp)
            if tmp.stat().st_size != st.st_size:
                raise IOError('size mismatch after copy')
            tmp.rename(d)
            done += 1
            nbytes += st.st_size
        except Exception as e:
            failed += 1
            log.write(f'FAIL {rel}: {e}\n')
        if i % 50 == 0 or i == len(rels):
            mbps = nbytes / 1e6 / max(time.time() - t0, 1)
            line = (f'{time.strftime("%H:%M:%S")} {i}/{len(rels)} copied={done} already={skipped} '
                    f'missing={missing} failed={failed} {nbytes / 1e9:.1f}GB @ {mbps:.1f}MB/s')
            print(line, flush=True)
            log.write(line + '\n')
            log.flush()
    print('COPY_DONE', flush=True)


if __name__ == '__main__':
    main()
