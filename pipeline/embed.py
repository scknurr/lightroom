"""CLIP ViT-L/14 embeddings, LAION aesthetic score, and zero-shot tags for every thumbnail."""
import argparse
import json
import warnings

import numpy as np
import open_clip
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from config import WORK, thumb_path, work_db
from vocab import CATEGORIES, PROMPT

warnings.filterwarnings('ignore')
MODELS = WORK / 'models'
EMB = WORK / 'embeddings'


class Aesthetic(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = torch.nn.Sequential(
            torch.nn.Linear(768, 1024), torch.nn.Dropout(0.2), torch.nn.Linear(1024, 128), torch.nn.Dropout(0.2),
            torch.nn.Linear(128, 64), torch.nn.Dropout(0.1), torch.nn.Linear(64, 16), torch.nn.Linear(16, 1))

    def forward(self, x):
        return self.layers(x)


class Thumbs(Dataset):
    def __init__(self, ids, pre):
        self.ids, self.pre = ids, pre

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, i):
        try:
            im = Image.open(thumb_path(self.ids[i])).convert('RGB')
            return self.pre(im), self.ids[i]
        except Exception:
            return torch.zeros(3, 224, 224), -self.ids[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', type=int, default=48)
    args = ap.parse_args()
    dev = 'mps' if torch.backends.mps.is_available() else 'cpu'
    model, _, pre = open_clip.create_model_and_transforms('ViT-L-14', pretrained='openai', cache_dir=str(MODELS / 'clip'))
    model = model.to(dev).eval()
    tok = open_clip.get_tokenizer('ViT-L-14')
    aes = Aesthetic()
    aes.load_state_dict(torch.load(MODELS / 'aesthetic_l14.pth', map_location='cpu'))
    aes = aes.to(dev).eval()

    with torch.no_grad():
        text = {}
        for cat, tags in CATEGORIES.items():
            t = model.encode_text(tok([PROMPT.format(x) for x in tags]).to(dev))
            text[cat] = t / t.norm(dim=-1, keepdim=True)

    db = work_db()
    db.execute('''CREATE TABLE IF NOT EXISTS clip (image_id INTEGER PRIMARY KEY, aesthetic REAL, junk TEXT,
                  junk_p REAL, tags TEXT, shard TEXT, row INTEGER)''')
    ids = [r[0] for r in db.execute('''SELECT image_id FROM thumbs WHERE source NOT IN ('fail','skip_video')
                                       AND image_id NOT IN (SELECT image_id FROM clip) ORDER BY image_id''')]
    print(f'{len(ids):,} to embed on {dev}', flush=True)
    EMB.mkdir(parents=True, exist_ok=True)
    loader = DataLoader(Thumbs(ids, pre), batch_size=args.batch, num_workers=8)
    shard_vecs, shard_ids, shard_no, done = [], [], len(list(EMB.glob('shard_*.npy'))), 0

    def flush():
        nonlocal shard_vecs, shard_ids, shard_no
        if not shard_vecs:
            return
        name = f'shard_{shard_no:04d}'
        np.save(EMB / f'{name}.npy', np.concatenate(shard_vecs).astype(np.float16))
        np.save(EMB / f'{name}_ids.npy', np.array(shard_ids, dtype=np.int64))
        db.executemany('UPDATE clip SET shard=?, row=? WHERE image_id=?',
                       [(name, i, iid) for i, iid in enumerate(shard_ids)])
        db.commit()
        shard_vecs, shard_ids, shard_no = [], [], shard_no + 1

    with torch.no_grad():
        for x, bid in loader:
            ok = bid > 0
            if not ok.any():
                continue
            x, bid = x[ok].to(dev), bid[ok].tolist()
            e = model.encode_image(x)
            e = e / e.norm(dim=-1, keepdim=True)
            score = aes(e).squeeze(1).cpu().tolist()
            probs = {cat: (100 * e @ t.T).softmax(dim=-1).cpu().numpy() for cat, t in text.items()}
            rows = []
            for i, iid in enumerate(bid):
                tags = {}
                for cat, p in probs.items():
                    if cat == 'junk':
                        continue
                    order = np.argsort(-p[i])[:3]
                    tags[cat] = [[CATEGORIES[cat][j], round(float(p[i][j]), 3)] for j in order if p[i][j] > 0.08]
                jp = probs['junk'][i]
                j = int(np.argmax(jp))
                rows.append((iid, score[i], CATEGORIES['junk'][j], float(1 - jp[0]), json.dumps(tags), None, None))
            db.executemany('INSERT OR REPLACE INTO clip VALUES (?,?,?,?,?,?,?)', rows)
            db.commit()
            shard_vecs.append(e.cpu().numpy())
            shard_ids.extend(bid)
            done += len(bid)
            if len(shard_ids) >= 20000:
                flush()
            if done % (args.batch * 40) < args.batch:
                print(f'{done:,}/{len(ids):,}', flush=True)
    flush()
    db.commit()


if __name__ == '__main__':
    main()
