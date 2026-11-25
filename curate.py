"""Find the best shots in the backlog (roadmap step 3, part 2).

Read-only with respect to Lightroom: reads the audit snapshot, clip.sqlite from
embed.py and (if present) cull.sqlite from cull.py. Writes curate.sqlite,
curate-summary.json and curate.html next to thumbs.sqlite.

For every embedded image:
  aesthetic   LAION aesthetic predictor (1-10, photos mostly land 4-7)
  taste       how much it resembles the shots you picked or rated in Lightroom
              (logistic regression on CLIP embeddings; needs >= --min-labels picks)
  tags        zero-shot CLIP tags in a few groups: subject, setting, light, and
              a junk check (screenshots, documents, receipts, accidental frames)
  theme       k-means cluster of similar photos, named by its closest tags
  score       blend of aesthetic, taste and cull.py's technical score (0-100)

Candidates are the top --top images by score that you have not rated or picked
yet, skipping cull rejects (soft / exposure / burst alternates) and junk,
capping each theme so one subject can't flood the list, and skipping
near-duplicates of a frame already chosen.

Re-run freely: everything here is computed from cached embeddings in a minute
or two, so weights and thresholds can be tuned.

Usage:
  .venv/bin/python curate.py 2026-10-05
"""
import argparse
import collections
import hashlib
import html
import json
import math
import random
import sqlite3
import urllib.request
from pathlib import Path

import numpy as np

from embed import load_clip, load_embeddings

AESTHETIC_URL = 'https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/sa_0_4_vit_b_32_linear.pth'
AESTHETIC_SHA256 = 'c7b14cead230694acc7b9447974d3cad78003c72da032e402a303b6c2429e85f'
CACHE = Path.home() / '.cache' / 'lightroom-rescue'

TEMPLATES = ['a photo of {}.', 'a photograph of {}.', 'a picture of {}.']

# Each group is a softmax over its labels; the winner is kept if it is confident.
TAG_GROUPS = {
    'subject': {
        'portrait': 'a portrait of a person',
        'people': 'a group of people',
        'child': 'a child',
        'baby': 'a baby',
        'wedding': 'a wedding',
        'party': 'people at a party',
        'concert': 'a concert or live music',
        'sports': 'people playing sports',
        'dog': 'a dog',
        'cat': 'a cat',
        'horse': 'a horse',
        'bird': 'a bird',
        'wildlife': 'a wild animal',
        'insect': 'an insect or butterfly',
        'fish': 'fish or sea life',
        'flowers': 'flowers',
        'plants': 'plants or trees',
        'food': 'food or a meal',
        'drink': 'a drink or cocktail',
        'car': 'a car',
        'motorcycle': 'a motorcycle',
        'aircraft': 'an airplane',
        'boat': 'a boat or ship',
        'train': 'a train',
        'building': 'a building or architecture',
        'interior': 'the inside of a room',
        'monument': 'a famous landmark or monument',
        'art': 'a painting, sculpture or artwork',
        'sign': 'a sign or text',
        'landscape': 'a landscape',
        'cityscape': 'a city skyline',
        'street': 'a street scene',
        'sky': 'the sky and clouds',
        'abstract': 'an abstract pattern or texture',
        'object': 'an object or still life',
        'electronics': 'a computer or electronics',
    },
    'setting': {
        'beach': 'a beach',
        'ocean': 'the ocean',
        'lake': 'a lake',
        'river': 'a river or stream',
        'waterfall': 'a waterfall',
        'mountains': 'mountains',
        'forest': 'a forest',
        'desert': 'a desert',
        'snow': 'snow',
        'field': 'fields or farmland',
        'garden': 'a garden or park',
        'city': 'a city',
        'town': 'a small town or village',
        'home': 'a home',
        'restaurant': 'a restaurant or bar',
        'stage': 'a stage',
        'stadium': 'a stadium or sports field',
        'road': 'a road',
        'underwater': 'underwater',
        'studio': 'a photo studio backdrop',
    },
    'light': {
        'golden_hour': 'warm golden sunlight',
        'sunset': 'a sunset or sunrise',
        'blue_hour': 'twilight blue hour',
        'night': 'night time',
        'daylight': 'bright daylight',
        'overcast': 'an overcast day',
        'fog': 'fog or mist',
        'rain': 'rain',
        'indoor_light': 'indoor lighting',
        'black_and_white': 'black and white',
    },
    # Junk labels compete against every subject and setting label above, so an
    # ordinary close-up isn't mistaken for a screenshot just for being unusual.
    'junk': {
        'screenshot': 'a screenshot of a phone or computer screen',
        'document': 'a document, receipt or sheet of paper',
        'whiteboard': 'a whiteboard or presentation slide',
        'accidental': 'an accidental blurry photo of the floor or a pocket',
        'meme': 'a meme or image with text',
        'qr': 'a barcode or QR code',
    },
}
CONFIDENCE = {'subject': 0.35, 'setting': 0.45, 'light': 0.45, 'junk': 0.65}


# ---------------------------------------------------------------- models

def aesthetic_head():
    import torch
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / 'sa_0_4_vit_b_32_linear.pth'
    if not p.exists():
        print('Downloading LAION aesthetic predictor (3 KB)...', flush=True)
        tmp = p.with_suffix('.tmp')
        urllib.request.urlretrieve(AESTHETIC_URL, tmp)
        if hashlib.sha256(tmp.read_bytes()).hexdigest() != AESTHETIC_SHA256:
            tmp.unlink()
            raise SystemExit(f'Aesthetic predictor download from {AESTHETIC_URL} failed its checksum.')
        tmp.replace(p)
    sd = torch.load(p, map_location='cpu', weights_only=True)
    return sd['weight'].numpy().astype(np.float32).reshape(-1), float(sd['bias'].numpy().reshape(-1)[0])


def text_embeddings(model, tokenizer, phrases):
    import torch
    out = []
    with torch.inference_mode():
        for ph in phrases:
            t = model.encode_text(tokenizer([tpl.format(ph) for tpl in TEMPLATES]))
            t = t / t.norm(dim=-1, keepdim=True)
            m = t.mean(0)
            out.append((m / m.norm()).numpy())
    return np.vstack(out).astype(np.float32)


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def percentile_rank(v):
    """0..1 rank of each value (ties averaged); NaN stays NaN."""
    out = np.full(len(v), np.nan)
    ok = ~np.isnan(v)
    if ok.sum():
        order = v[ok].argsort(kind='stable')
        r = np.empty(ok.sum())
        r[order] = np.arange(ok.sum())
        out[ok] = r / max(ok.sum() - 1, 1)
    return out


# ---------------------------------------------------------------- data

def load_catalog(out):
    snap = out / 'catalog-audit-snapshot.sqlite'
    if Path(str(snap) + '-wal').exists():
        raise SystemExit('Unexpected snapshot WAL; refusing immutable read.')
    c = sqlite3.connect(snap.as_uri() + '?mode=ro&immutable=1', uri=True)
    c.execute('PRAGMA query_only=ON')
    q = '''SELECT i.id_local, i.captureTime, i.pick, i.rating, af.idx_filename
           FROM Adobe_images i JOIN AgLibraryFile af ON af.id_local = i.rootFile
           WHERE i.masterImage IS NULL'''
    imgs = {iid: {'capture': ct or '', 'pick': pick or 0, 'rating': rating or 0, 'name': name or ''}
            for iid, ct, pick, rating, name in c.execute(q)}
    c.close()
    return imgs


def load_cull(tdir):
    p = tdir / 'cull.sqlite'
    if not p.exists():
        return {}
    c = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
    try:
        return {iid: (verdict, tech) for iid, verdict, tech in c.execute('SELECT image_id, verdict, tech_score FROM cull')}
    except sqlite3.OperationalError:
        return {}
    finally:
        c.close()


# ---------------------------------------------------------------- taste model

def train_taste(x, imgs, ids, a):
    """Learn 'looks like something you kept' from Lightroom picks/ratings.

    Positives: picked, or rated >= --pos-rating. Negatives: rejected or rated
    below that, plus a sample of never-rated images (mostly not keepers, but
    not all, so they get a lower weight).
    """
    from sklearn.base import clone
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score

    pick = np.array([imgs[i]['pick'] for i in ids])
    rating = np.array([imgs[i]['rating'] for i in ids])
    pos = (pick > 0) | (rating >= a.pos_rating)
    neg = ~pos & ((pick < 0) | ((rating > 0) & (rating < a.pos_rating)))
    unl = ~pos & ~neg & (pick == 0) & (rating == 0)
    report = {'positives': int(pos.sum()), 'explicit_negatives': int(neg.sum())}
    if pos.sum() < a.min_labels:
        report['skipped'] = f'need at least {a.min_labels} picked/rated images, found {int(pos.sum())}'
        return None, report

    rng = np.random.default_rng(0)
    unl_idx = np.flatnonzero(unl)
    n_bg = min(len(unl_idx), max(3 * int(pos.sum()), 2000))
    bg = rng.choice(unl_idx, n_bg, replace=False) if n_bg else np.zeros(0, int)
    pos_idx, neg_idx = np.flatnonzero(pos), np.flatnonzero(neg)
    train = np.concatenate([pos_idx, neg_idx, bg])
    y = np.concatenate([np.ones(len(pos_idx)), np.zeros(len(neg_idx) + len(bg))])
    w = np.concatenate([np.ones(len(pos_idx) + len(neg_idx)), np.full(len(bg), 0.5)])
    if len(np.unique(y)) < 2:
        report['skipped'] = 'no negatives or unrated images to contrast with'
        return None, report

    clf = LogisticRegression(C=a.taste_c, class_weight='balanced', max_iter=2000)
    folds = min(5, int(min(y.sum(), len(y) - y.sum())))
    if folds >= 2:
        p = np.zeros(len(y))
        for tr, te in StratifiedKFold(folds, shuffle=True, random_state=0).split(x[train], y):
            p[te] = clone(clf).fit(x[train][tr], y[tr], sample_weight=w[tr]).predict_proba(x[train][te])[:, 1]
        report['cv_auc_vs_unrated'] = round(float(roc_auc_score(y, p)), 3)
        if len(neg_idx) >= 5:
            m = np.concatenate([np.ones(len(pos_idx) + len(neg_idx), bool), np.zeros(len(bg), bool)])
            report['cv_auc_vs_your_rejects'] = round(float(roc_auc_score(y[m], p[m])), 3)
    clf.fit(x[train], y, sample_weight=w)
    report['background_sample'] = int(len(bg))
    return clf.predict_proba(x)[:, 1], report


# ---------------------------------------------------------------- selection

def pick_candidates(order, x, cluster, k, a):
    cap = max(3, math.ceil(a.top / max(k, 1) * a.theme_cap))
    per = collections.Counter()
    chosen = []
    chosen_vecs = np.zeros((0, x.shape[1]), np.float32)
    for i in order:
        if len(chosen) >= a.top:
            break
        if per[cluster[i]] >= cap:
            continue
        if len(chosen) and float((chosen_vecs @ x[i]).max()) > a.dupe_sim:
            continue
        chosen.append(i)
        per[cluster[i]] += 1
        chosen_vecs = np.vstack([chosen_vecs, x[i:i + 1]])
    return chosen


# ---------------------------------------------------------------- page

def page(summary, cards, themes, junk, rel):
    def fig(r, extra=''):
        tip = (f"#{r['id']} {r['name']}  {r['capture']}\n"
               f"score {r['score']:.0f} · aesthetic {r['aes']:.2f}"
               + (f" · taste {r['taste']:.2f}" if r['taste'] is not None else '')
               + (f" · tech {r['tech']:.0f}" if r['tech'] is not None else '')
               + f"\n{r['tags']}  [{r['theme']}]")
        return (f'<figure><img loading="lazy" src="{html.escape(rel(r["id"]))}" title="{html.escape(tip)}">'
                f'<figcaption>{extra}{html.escape(r["tags"])}</figcaption></figure>')

    def grid(rows, extra=lambda r: ''):
        return '<div class="grid">' + ''.join(fig(r, extra(r)) for r in rows) + '</div>'

    t = summary['taste_model']
    taste_line = (f"Taste model: {t['positives']:,} of your picks/ratings, "
                  + (f"cross-validated AUC {t.get('cv_auc_vs_unrated', '–')} vs unrated"
                     + (f", {t['cv_auc_vs_your_rejects']} vs your rejects" if 'cv_auc_vs_your_rejects' in t else '')
                     if 'skipped' not in t else f"skipped ({t['skipped']})"))
    th = ''.join(
        f'<h3>{html.escape(name)} <span>{size:,} photos</span></h3>' + grid(rows)
        for name, size, rows in themes)
    tags = ' · '.join(f'{k} {v:,}' for k, v in list(summary['tags'].items())[:40])
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backlog Picks</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1400px;padding:16px}}
h1{{margin:0 0 4px}} h2{{margin:36px 0 8px}} h3{{margin:20px 0 6px;font-size:14px}} h2 span,h3 span,.sub{{color:var(--mut);font-weight:400;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}}
figure{{margin:0}} img{{width:100%;aspect-ratio:1;object-fit:contain;background:#0001;border-radius:4px;display:block}}
figcaption{{font-size:11px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
b{{color:var(--fg)}}
</style></head><body><h1>Backlog picks</h1>
<p class="sub">{summary["embedded"]:,} photos scored · {summary["candidates"]:,} candidates from {summary["eligible"]:,} unrated keepers ·
{html.escape(taste_line)} · weights aesthetic {summary["weights"]["aesthetic"]}, taste {summary["weights"]["taste"]}, technical {summary["weights"]["technical"]} · hover a photo for its numbers</p>
<h2>Top candidates <span>(not yet rated or picked in Lightroom)</span></h2>
{grid(cards, lambda r: f"<b>{r['rank']}</b> · ")}
<h2>Themes <span>(best few per cluster of similar photos)</span></h2>{th}
<h2>Likely junk <span>(screenshots, documents, accidental frames; excluded from candidates)</span></h2>
{grid(junk, lambda r: f"<b>{html.escape(r['junk'])}</b> · ")}
<h2>Tag counts</h2><p class="sub">{html.escape(tags)}</p>
</body></html>'''


# ---------------------------------------------------------------- driver

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('output', type=Path, help='Audit output folder (e.g. 2026-10-05).')
    ap.add_argument('--thumbs', type=Path, help='Folder holding thumbs/, clip.sqlite, cull.sqlite (default: the output folder).')
    ap.add_argument('--top', type=int, default=1000, help='How many candidates to pick (default 1000).')
    ap.add_argument('--clusters', type=int, default=80, help='Number of themes (default 80).')
    ap.add_argument('--w-aesthetic', type=float, default=0.4)
    ap.add_argument('--w-taste', type=float, default=0.4)
    ap.add_argument('--w-technical', type=float, default=0.2)
    ap.add_argument('--pos-rating', type=int, default=3, help='Star rating that counts as a keeper (default 3).')
    ap.add_argument('--min-labels', type=int, default=30, help='Minimum keepers needed to train the taste model.')
    ap.add_argument('--taste-c', type=float, default=1.0, help='Taste model regularization (smaller = smoother).')
    ap.add_argument('--theme-cap', type=float, default=3.0,
                    help='Max candidates per theme, as a multiple of an even share (default 3).')
    ap.add_argument('--dupe-sim', type=float, default=0.93,
                    help='Skip candidates this similar (cosine) to one already chosen (default 0.93).')
    ap.add_argument('--include-rated', action='store_true', help='Also consider images you already rated or picked.')
    a = ap.parse_args()

    out = a.output.resolve()
    tdir = (a.thumbs or a.output).resolve()
    if not (tdir / 'clip.sqlite').exists():
        raise SystemExit(f'No clip.sqlite in {tdir}; run embed.py first.')

    print('Loading catalog, embeddings and cull results...', flush=True)
    imgs = load_catalog(out)
    ids, x, model_name = load_embeddings(tdir)
    keep = np.array([i in imgs for i in ids], bool)
    ids, x = ids[keep], x[keep]
    n = len(ids)
    if n == 0:
        raise SystemExit('No embeddings match catalog images.')
    cull = load_cull(tdir)

    # Aesthetic
    import torch
    torch.set_grad_enabled(False)
    if model_name != 'ViT-B-32-quickgelu/openai':
        raise SystemExit(f'The aesthetic predictor needs OpenAI ViT-B/32 embeddings, not {model_name}.')
    w, b = aesthetic_head()
    aes = x @ w + b

    # Zero-shot tags
    print('Tagging...', flush=True)
    clip_model, _, tokenizer = load_clip(*model_name.split('/'))
    scale = float(clip_model.logit_scale.exp())
    tags = [[] for _ in range(n)]
    junk = np.array([''] * n, dtype=object)
    label_vecs, label_names, group_vecs = [], [], {}
    for group, labels in TAG_GROUPS.items():
        keys = list(labels)
        t = text_embeddings(clip_model, tokenizer, [labels[k] for k in keys])
        group_vecs[group] = t
        if group == 'junk':
            continue
        label_vecs.append(t)
        label_names += keys
        p = softmax(scale * (x @ t.T))
        top = p.argmax(1)
        conf = p[np.arange(n), top]
        for i in np.flatnonzero(conf >= CONFIDENCE[group]):
            tags[i].append(keys[top[i]])
    junk_keys = list(TAG_GROUPS['junk'])
    real = np.vstack([group_vecs['subject'], group_vecs['setting']])
    p = softmax(scale * (x @ np.vstack([group_vecs['junk'], real]).T))[:, :len(junk_keys)]
    for i in np.flatnonzero(p.sum(1) >= CONFIDENCE['junk']):
        junk[i] = junk_keys[p[i].argmax()]
    label_vecs = np.vstack(label_vecs)

    # Themes
    print('Clustering themes...', flush=True)
    from sklearn.cluster import MiniBatchKMeans
    k = max(1, min(a.clusters, n // 5))
    km = MiniBatchKMeans(n_clusters=k, random_state=0, batch_size=4096, n_init=3).fit(x)
    cluster = km.labels_
    cent = km.cluster_centers_ / (np.linalg.norm(km.cluster_centers_, axis=1, keepdims=True) + 1e-8)
    theme_names = []
    for c in range(k):
        s = cent[c] @ label_vecs.T
        best = s.argsort()[::-1][:2]
        theme_names.append(' / '.join(label_names[j] for j in best))

    # Taste
    print('Learning your taste from Lightroom picks and ratings...', flush=True)
    taste, taste_report = train_taste(x, imgs, ids, a)

    # Blend
    tech = np.array([cull.get(int(i), (None, None))[1] for i in ids], dtype=float)
    verdict = [cull.get(int(i), (None, None))[0] for i in ids]
    parts = [(a.w_aesthetic, percentile_rank(aes))]
    if taste is not None:
        parts.append((a.w_taste, percentile_rank(taste)))
    if not np.isnan(tech).all():
        parts.append((a.w_technical, np.nan_to_num(tech / 100, nan=0.5)))
    wsum = sum(wt for wt, _ in parts) or 1.0
    score = 100 * sum(wt * v for wt, v in parts) / wsum

    rated = np.array([imgs[int(i)]['pick'] != 0 or imgs[int(i)]['rating'] > 0 for i in ids])
    eligible = np.array([(a.include_rated or not rated[j]) and not junk[j]
                         and verdict[j] in (None, 'keep') for j in range(n)])
    order = [j for j in np.argsort(-score) if eligible[j]]
    chosen = pick_candidates(order, x, cluster, k, a)
    rank = {j: r for r, j in enumerate(chosen, 1)}

    # Save
    db = sqlite3.connect(tdir / 'curate.sqlite')
    db.execute('DROP TABLE IF EXISTS curate')
    db.execute('''CREATE TABLE curate (image_id INTEGER PRIMARY KEY, score REAL, aesthetic REAL, taste REAL,
        tech_score REAL, tags TEXT, junk TEXT, theme INT, theme_name TEXT, candidate_rank INT)''')
    db.executemany('INSERT INTO curate VALUES (?,?,?,?,?,?,?,?,?,?)', [
        (int(ids[j]), round(float(score[j]), 2), round(float(aes[j]), 3),
         None if taste is None else round(float(taste[j]), 4),
         None if np.isnan(tech[j]) else float(tech[j]), ' '.join(tags[j]), junk[j] or None,
         int(cluster[j]), theme_names[cluster[j]], rank.get(j)) for j in range(n)])
    db.execute('DROP TABLE IF EXISTS themes')
    db.execute('CREATE TABLE themes (theme INTEGER PRIMARY KEY, name TEXT, size INT)')
    sizes = collections.Counter(cluster.tolist())
    db.executemany('INSERT INTO themes VALUES (?,?,?)', [(c, theme_names[c], sizes[c]) for c in range(k)])
    db.commit()
    db.close()

    def card(j):
        i = int(ids[j])
        return {'id': i, 'name': imgs[i]['name'], 'capture': imgs[i]['capture'], 'score': float(score[j]),
                'aes': float(aes[j]), 'taste': None if taste is None else float(taste[j]),
                'tech': None if np.isnan(tech[j]) else float(tech[j]), 'tags': ' '.join(tags[j]),
                'theme': theme_names[cluster[j]], 'junk': junk[j], 'rank': rank.get(j)}

    def rel(i):
        return f'thumbs/{i // 1000}/{i}.jpg'

    by_theme = collections.defaultdict(list)
    for j in np.argsort(-score):
        if eligible[j] or rated[j]:
            by_theme[cluster[j]].append(j)
    themes = [(theme_names[c], sizes[c], [card(j) for j in by_theme[c][:8]])
              for c, _ in sizes.most_common()]
    junk_idx = [j for j in range(n) if junk[j]]
    random.Random(1).shuffle(junk_idx)

    tag_counts = collections.Counter(t for ts in tags for t in ts)
    summary = {
        'embedded': n,
        'model': model_name,
        'eligible': int(eligible.sum()),
        'candidates': len(chosen),
        'weights': {'aesthetic': a.w_aesthetic, 'taste': a.w_taste if taste is not None else 0,
                    'technical': a.w_technical if not np.isnan(tech).all() else 0},
        'aesthetic_percentiles': {str(p): round(float(np.percentile(aes, p)), 2) for p in (10, 50, 90, 99)},
        'taste_model': taste_report,
        'junk': dict(collections.Counter(j for j in junk if j)),
        'tags': dict(tag_counts.most_common()),
        'themes': [{'name': theme_names[c], 'size': s} for c, s in sizes.most_common()],
    }
    (tdir / 'curate-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (tdir / 'curate.html').write_text(
        page(summary, [card(j) for j in chosen[:300]], themes, [card(j) for j in junk_idx[:48]], rel), encoding='utf-8')
    brief = {k2: summary[k2] for k2 in ('embedded', 'eligible', 'candidates', 'weights', 'aesthetic_percentiles', 'taste_model', 'junk')}
    print(json.dumps(brief, indent=2))
    print(f'{len(sizes)} themes, {len(tag_counts)} distinct tags. Page: {tdir / "curate.html"}')


if __name__ == '__main__':
    main()
