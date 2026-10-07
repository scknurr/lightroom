"""Re-score privacy from saved CLIP embeddings using paired contrast prompts (no image re-read)."""
import warnings

import numpy as np
import open_clip
import torch

from config import WORK, work_db

warnings.filterwarnings('ignore')

# First prompt is the positive; the rest are the alternatives it must beat.
PAIRS = {
    'nudity': ('a photo of a naked person, nudity', 'a photo of people wearing clothes',
               'a photo with no people in it'),
    'underwear': ('a photo of a person in underwear or lingerie', 'a photo of a person in everyday clothes',
                  'a photo with no people in it'),
    'document': ('a close-up photo of an identity card, passport or credit card', 'a photo of people',
                 'a photo of a place or a thing'),
}


def main():
    model, _, _ = open_clip.create_model_and_transforms('ViT-L-14', pretrained='openai', cache_dir=str(WORK / 'models' / 'clip'))
    tok = open_clip.get_tokenizer('ViT-L-14')
    with torch.no_grad():
        text = {}
        for k, pair in PAIRS.items():
            t = model.encode_text(tok(list(pair)))
            text[k] = (t / t.norm(dim=-1, keepdim=True)).numpy()
    db = work_db()
    for col in ['priv_nudity', 'priv_underwear', 'priv_document']:
        if col not in [r[1] for r in db.execute('PRAGMA table_info(clip)')]:
            db.execute(f'ALTER TABLE clip ADD COLUMN {col} REAL')
    for shard in sorted((WORK / 'embeddings').glob('shard_*[0-9].npy')):
        ids = np.load(str(shard).replace('.npy', '_ids.npy'))
        e = np.load(shard).astype(np.float32)
        out = {}
        for k, t in text.items():
            logits = 100 * e @ t.T
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            out[k] = p[:, 0] / p.sum(axis=1)
        db.executemany('UPDATE clip SET priv_nudity=?, priv_underwear=?, priv_document=? WHERE image_id=?',
                       [(float(out['nudity'][i]), float(out['underwear'][i]), float(out['document'][i]), int(iid))
                        for i, iid in enumerate(ids)])
        db.commit()
    for k in PAIRS:
        v = np.array([r[0] for r in db.execute(f'SELECT priv_{k} FROM clip')])
        print(k, 'pct 50/90/97/99/99.5:', np.percentile(v, [50, 90, 97, 99, 99.5]).round(3), '>0.5:', int((v > 0.5).sum()))


if __name__ == '__main__':
    main()
