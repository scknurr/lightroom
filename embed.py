"""CLIP embeddings for every thumbnail (roadmap step 3, part 1).

Read-only with respect to Lightroom: reads only the thumbnail cache from
build_thumbs.py and writes clip.sqlite next to thumbs.sqlite.

Each embedding is a 512-number fingerprint of what the photo shows. curate.py
turns them into an aesthetic score, zero-shot tags, theme clusters and a model
of your own taste (learned from your Lightroom picks and ratings).

The default model (OpenAI CLIP ViT-B/32) is the one the LAION aesthetic
predictor was trained on, and is small enough to run on a CPU: expect a few
dozen images per second on a 4-core Intel Mac. Weights (~350 MB) download on
first run to ~/.cache.

Resumable: rerun the same command and finished images are skipped; images
whose thumbnail was rebuilt are re-embedded.

Usage:
  .venv/bin/python embed.py 2026-10-05
"""
import argparse
import os
import sqlite3
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

MODEL = 'ViT-B-32-quickgelu'
PRETRAINED = 'openai'


def load_clip(model=MODEL, pretrained=PRETRAINED):
    import open_clip
    m, _, preprocess = open_clip.create_model_and_transforms(model, pretrained=pretrained)
    m.eval()
    return m, preprocess, open_clip.get_tokenizer(model)


def open_db(tdir, model, pretrained):
    db = sqlite3.connect(tdir / 'clip.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS emb (image_id INTEGER PRIMARY KEY, thumb_updated REAL, vec BLOB)')
    want = f'{model}/{pretrained}'
    have = db.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    if have and have[0] != want:
        raise SystemExit(f'clip.sqlite holds {have[0]} embeddings, not {want}. Move it aside to switch models.')
    db.execute("INSERT OR REPLACE INTO meta VALUES ('model', ?)", (want,))
    db.commit()
    return db


def load_embeddings(tdir):
    """-> (image_ids int64[N], float32[N, D] L2-normalized, 'model/pretrained')."""
    db = sqlite3.connect((tdir / 'clip.sqlite').as_uri() + '?mode=ro', uri=True)
    model = db.execute("SELECT value FROM meta WHERE key='model'").fetchone()[0]
    ids, vecs = [], []
    for iid, vec in db.execute('SELECT image_id, vec FROM emb ORDER BY image_id'):
        ids.append(iid)
        vecs.append(np.frombuffer(vec, dtype=np.float16))
    db.close()
    if not ids:
        return np.zeros(0, np.int64), np.zeros((0, 512), np.float32), model
    x = np.vstack(vecs).astype(np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-8
    return np.array(ids, dtype=np.int64), x, model


class Thumbs(torch.utils.data.Dataset):
    def __init__(self, jobs, preprocess):
        self.jobs, self.preprocess = jobs, preprocess

    def __len__(self):
        return len(self.jobs)

    def __getitem__(self, i):
        iid, path, upd = self.jobs[i]
        try:
            with Image.open(path) as im:
                x = self.preprocess(im.convert('RGB'))
            ok = True
        except Exception:
            x, ok = torch.zeros(3, 224, 224), False
        return x, iid, upd, ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('output', type=Path, help='Audit output folder (e.g. 2026-10-05).')
    ap.add_argument('--thumbs', type=Path, help='Folder holding thumbs/ and thumbs.sqlite (default: the output folder).')
    ap.add_argument('--batch', type=int, default=64)
    ap.add_argument('--threads', type=int, default=os.cpu_count() or 4, help='Torch CPU threads.')
    ap.add_argument('--loaders', type=int, default=2, help='Background processes decoding thumbnails.')
    ap.add_argument('--limit', type=int, help='Only embed this many pending thumbnails (for testing).')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--pretrained', default=PRETRAINED)
    a = ap.parse_args()

    tdir = (a.thumbs or a.output).resolve()
    if not (tdir / 'thumbs.sqlite').exists():
        raise SystemExit(f'No thumbs.sqlite in {tdir}; run build_thumbs.py first.')
    tm = sqlite3.connect((tdir / 'thumbs.sqlite').as_uri() + '?mode=ro', uri=True)
    thumbs = dict(tm.execute("SELECT image_id, MAX(updated) FROM thumbs WHERE status='ok' GROUP BY 1"))
    tm.close()

    db = open_db(tdir, a.model, a.pretrained)
    have = dict(db.execute('SELECT image_id, thumb_updated FROM emb'))
    pending = [(iid, str(tdir / 'thumbs' / str(iid // 1000) / f'{iid}.jpg'), upd)
               for iid, upd in sorted(thumbs.items()) if have.get(iid) != upd]
    print(f'{len(thumbs):,} thumbnails; {len(thumbs) - len(pending):,} already embedded; '
          f'{len(pending):,} to embed.', flush=True)
    if a.limit:
        pending = pending[:a.limit]
    if not pending:
        return

    torch.set_num_threads(a.threads)
    print(f'Loading {a.model} ({a.pretrained})...', flush=True)
    model, preprocess, _ = load_clip(a.model, a.pretrained)
    loader = torch.utils.data.DataLoader(Thumbs(pending, preprocess), batch_size=a.batch,
                                         num_workers=a.loaders, persistent_workers=a.loaders > 0)
    done = failed = 0
    started = last = time.monotonic()
    try:
        with torch.inference_mode():
            for x, ids, upds, oks in loader:
                f = model.encode_image(x)
                f = (f / f.norm(dim=-1, keepdim=True)).numpy().astype(np.float16)
                rows = [(int(i), float(u), f[k].tobytes()) for k, (i, u, ok) in enumerate(zip(ids, upds, oks)) if ok]
                db.executemany('INSERT OR REPLACE INTO emb VALUES (?,?,?)', rows)
                db.commit()
                done += len(rows)
                failed += len(ids) - len(rows)
                now = time.monotonic()
                if now - last > 15 or done + failed == len(pending):
                    rate = (done + failed) / max(now - started, 1e-6)
                    print(f'{done + failed:,}/{len(pending):,}  {rate:.0f}/s  '
                          f'eta {(len(pending) - done - failed) / rate / 60:.0f} min  unreadable={failed}', flush=True)
                    last = now
    except KeyboardInterrupt:
        print('\nInterrupted; progress saved. Rerun the same command to resume.', flush=True)
    db.close()
    print(f'Embedded {done:,}; unreadable {failed:,}.')


if __name__ == '__main__':
    main()
