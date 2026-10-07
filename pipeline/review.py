"""Render a local HTML review page for the current selection (thumbnails served from the G DRIVE cache)."""
import argparse
import html
import json
import urllib.parse

from config import WORK, thumb_path, work_db


def src(iid):
    return 'file://' + urllib.parse.quote(str(thumb_path(iid)))


def card(r, extra=''):
    iid, score, tier, reasons, aes, taste, sharp, tags, pick, rating, cap = r
    tl = []
    for cat in ('subject', 'scene', 'light', 'style'):
        tl += [t for t, _ in json.loads(tags or '{}').get(cat, [])[:1]]
    badges = ''
    if pick == 1:
        badges += '<span class="b pick">your pick</span>'
    if rating and rating >= 3:
        badges += f'<span class="b star">{"★" * rating}</span>'
    if reasons:
        badges += f'<span class="b why">{html.escape(reasons)}</span>'
    return f'''<figure><a href="{src(iid)}" target="_blank"><img loading="lazy" src="{src(iid)}"></a>
      <figcaption>{badges}<div class="m">#{iid} · {cap[:10] if cap else ''} · score {score:+.2f}</div>
      <div class="m">aes {aes:.2f} · taste {taste if taste is None else f"{taste:+.2f}"} · sharp {sharp:.0f}</div>
      <div class="t">{html.escape(" · ".join(tl))}</div>{extra}</figcaption></figure>'''


Q = '''SELECT s.image_id, s.score, s.tier, s.reasons, c.aesthetic, c.taste, t.sharp_top3, c.tags, i.pick, i.rating,
          i.capture_time FROM selection s JOIN clip c USING(image_id) JOIN technical t USING(image_id)
          JOIN images i USING(image_id) '''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', default='2019')
    ap.add_argument('--out', default=str(WORK / 'review.html'))
    args = ap.parse_args()
    db = work_db()
    y = args.year
    tiers = dict(db.execute('SELECT s.tier, COUNT(*) FROM selection s JOIN images i USING(image_id) WHERE i.year=? GROUP BY 1', (y,)))
    try:
        report = json.loads((WORK / 'taste_report.json').read_text())
    except FileNotFoundError:
        report = {}

    heroes = db.execute(Q + 'WHERE i.year=? AND s.tier="hero" ORDER BY s.score DESC LIMIT 120', (y,)).fetchall()
    gems = db.execute(Q + 'WHERE i.year=? AND s.tier IN ("hero","select") AND i.pick=0 AND i.rating<3 '
                          'ORDER BY s.score DESC LIMIT 60', (y,)).fetchall()
    total_picks = db.execute('SELECT COUNT(*) FROM images WHERE year=? AND pick=1', (y,)).fetchone()[0]
    picks_in_top = db.execute('SELECT COUNT(*) FROM selection s JOIN images i USING(image_id) WHERE i.year=? AND i.pick=1 '
                              'AND s.tier IN ("hero","select")', (y,)).fetchone()[0]
    picks_rejected = db.execute(Q + 'WHERE i.year=? AND i.pick=1 AND s.tier="reject" ORDER BY t.sharp_top3 LIMIT 30', (y,)).fetchall()

    sharp_vals = sorted(r[0] for r in db.execute('SELECT t.sharp_top3 FROM technical t JOIN images i USING(image_id) WHERE i.year=?', (y,)))
    ladder = ''
    if sharp_vals:
        for pct in (1, 3, 5, 8, 12, 18, 25, 40):
            v = sharp_vals[int(len(sharp_vals) * pct / 100)]
            rows = db.execute(Q + 'WHERE i.year=? AND t.sharp_top3 BETWEEN ? AND ? ORDER BY RANDOM() LIMIT 6',
                              (y, v * 0.93, v * 1.07)).fetchall()
            ladder += f'<h3>p{pct} · sharpness ≈ {v:.0f}</h3><div class="grid">{"".join(card(r) for r in rows)}</div>'

    rejects = ''
    for reason in ('blurry', 'too dark', 'blown out', 'screenshot', 'meme or graphic', 'document or receipt'):
        rows = db.execute(Q + 'WHERE i.year=? AND s.tier="reject" AND s.reasons LIKE ? ORDER BY RANDOM() LIMIT 12',
                          (y, f'%{reason}%')).fetchall()
        n = db.execute('SELECT COUNT(*) FROM selection s JOIN images i USING(image_id) WHERE i.year=? AND s.tier="reject" '
                       'AND s.reasons LIKE ?', (y, f'%{reason}%')).fetchone()[0]
        if rows:
            rejects += f'<h3>{reason} — {n:,}</h3><div class="grid">{"".join(card(r) for r in rows)}</div>'

    bursts_html = ''
    for (b,) in db.execute('SELECT s.burst FROM selection s JOIN images i USING(image_id) WHERE i.year=? AND s.burst_size BETWEEN 5 AND 14 '
                           'AND s.tier IN ("hero","select") ORDER BY s.score DESC LIMIT 4', (y,)):
        rows = db.execute(Q + 'WHERE s.burst=? ORDER BY i.capture_time', (b,)).fetchall()
        bursts_html += f'<div class="grid burst">{"".join(card(r) for r in rows)}</div>'

    tagcount = {}
    for (tags,) in db.execute('SELECT c.tags FROM clip c JOIN selection s USING(image_id) JOIN images i USING(image_id) '
                              'WHERE i.year=? AND s.tier IN ("hero","select")', (y,)):
        for cat, items in json.loads(tags).items():
            for t, p in items[:1]:
                tagcount[(cat, t)] = tagcount.get((cat, t), 0) + 1
    tag_html = ''.join(f'<span class="tag">{html.escape(t)} <b>{n}</b></span>'
                       for (cat, t), n in sorted(tagcount.items(), key=lambda kv: -kv[1])[:80])

    page = f'''<!doctype html><meta charset="utf-8"><title>Photo Review {y}</title>
<style>
:root{{--bg:#0f1013;--card:#17191e;--ink:#e8e9ec;--mut:#8f95a1;--acc:#f5b942}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px -apple-system,BlinkMacSystemFont,sans-serif}}
.wrap{{max-width:1500px;margin:0 auto;padding:24px 16px 80px}} h1{{margin:0}} h2{{margin:36px 0 6px}} h3{{color:var(--mut);font-weight:500;margin:18px 0 6px}}
.sub{{color:var(--mut)}} .stats{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}} .stat{{background:var(--card);padding:10px 14px;border-radius:10px}}
.stat b{{font-size:22px;display:block}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}}
figure{{margin:0;background:var(--card);border-radius:8px;overflow:hidden}} img{{width:100%;aspect-ratio:3/2;object-fit:cover;display:block;background:#000}}
figcaption{{padding:6px 8px 8px}} .m{{color:var(--mut);font-size:11px}} .t{{font-size:12px;margin-top:2px}}
.b{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;margin:0 4px 3px 0}}
.pick{{background:#1f3b2a;color:#7ee2a3}} .star{{background:#3b321a;color:var(--acc)}} .why{{background:#3b1f22;color:#f19aa0}}
.tag{{display:inline-block;background:var(--card);padding:3px 10px;border-radius:99px;margin:3px;font-size:12px}} .tag b{{color:var(--acc)}}
.burst{{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}} .burst + .burst{{margin-top:14px}}
</style><div class="wrap">
<h1>Photo review — {y}</h1>
<p class="sub">Pilot run. Score = generic aesthetic (LAION/CLIP) + your personal taste model (learned from your picks, ratings and edits) + a little sharpness. Click any photo to open it full-size.</p>
<div class="stats">{"".join(f'<div class="stat"><b>{n:,}</b>{t}</div>' for t, n in sorted(tiers.items(), key=lambda kv: -kv[1]))}
<div class="stat"><b>{picks_in_top:,}/{total_picks:,}</b>of your picks landed in hero/select</div>
<div class="stat"><b>{report.get("auc_aesthetic_within_shoot", ["–"])[0] if report else "–"}</b>generic aesthetic: how well it reproduces your picks within a shoot (0.5 = coin flip)</div>
<div class="stat"><b>{report.get("auc_taste_within_shoot_heldout", ["–"])[0] if report else "–"}</b>taste model on held-out shoots</div></div>

<h2>Heroes</h2><p class="sub">Top ~1.5% after culling blur and junk and keeping one frame per burst.</p>
<div class="grid">{"".join(card(r) for r in heroes)}</div>

<h2>Hidden gems</h2><p class="sub">Scored hero/select, but you never picked or rated them.</p>
<div class="grid">{"".join(card(r) for r in gems)}</div>

<h2>Best-of-burst examples</h2><p class="sub">Each row is one burst; the kept frame has no "burst alt" tier.</p>
{bursts_html}

<h2>Your picks that the cull rejected</h2><p class="sub">These show where the blur or exposure rules are too strict.</p>
<div class="grid">{"".join(card(r) for r in picks_rejected)}</div>

<h2>Rejects by reason</h2>{rejects}

<h2>Blur ladder</h2><p class="sub">Random frames at each sharpness percentile, used to set the blur threshold by eye.</p>{ladder}

<h2>Top tags among hero/select</h2><div>{tag_html}</div>
</div>'''
    from pathlib import Path
    Path(args.out).write_text(page)
    print('wrote', args.out)


if __name__ == '__main__':
    main()
