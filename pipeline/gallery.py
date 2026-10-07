"""Editor's Cut page for a year: curator notes, series, portfolio picks, Instagram queue (private photos excluded)."""
import argparse
import html
import json
import urllib.parse

from config import WORK, thumb_path


def src(iid):
    return 'file://' + urllib.parse.quote(str(thumb_path(iid)))


def tile(p, big=False, extra=''):
    t = html.escape(p.get('title', ''))
    notes = html.escape(p.get('treatment', {}).get('notes', ''))
    uses = ' · '.join(p.get('uses', []))
    rel = ', '.join(r.replace('_', ' ') for r in p.get('release_notes', []))
    return f'''<figure class="{'big' if big else ''}"><a href="{src(p['image_id'])}" target="_blank"><img loading="lazy" src="{src(p['image_id'])}"></a>
<figcaption><b>{t}</b>{extra}<div class="c">{html.escape(p.get('caption', ''))}</div>
<div class="m">#{p['image_id']} · editor {p.get('editor_score', '')}/10 · {html.escape(uses)}{(' · ' + html.escape(rel)) if rel else ''}</div>
{f'<div class="n">✎ {notes}</div>' if notes and big else ''}</figcaption></figure>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', default='2019')
    args = ap.parse_args()
    y = args.year
    ed = json.loads((WORK / f'editor_{y}.json').read_text())
    by = {p['image_id']: p for p in ed['photos'] if not p.get('private')}
    portfolio = sorted((p for p in by.values() if p['verdict'] == 'portfolio'), key=lambda p: -p['editor_score'])
    backstock = sum(1 for p in by.values() if p['verdict'] == 'backstock')
    series_html = ''
    for s in sorted(ed['series'], key=lambda s: -len(s['members'])):
        members = [by[i] for i in s['members'] if i in by]
        if not members:
            continue
        cover = by.get(s['cover_id'], members[0])
        rest = [m for m in members if m['image_id'] != cover['image_id']]
        series_html += f'''<section class="series"><h3>{html.escape(s['name'])} <span>{len(members)} photos · best for {html.escape(", ".join(s['best_for']))}</span></h3>
<p class="sub">{html.escape(s['description'])}</p><div class="sgrid">{tile(cover, big=True)}<div class="grid small">{"".join(tile(m) for m in rest[:11])}</div></div></section>'''
    ig = ''.join(tile(by[i], extra=f' <span class="num">{n}</span>') for n, i in enumerate(ed['instagram_queue'], 1) if i in by)
    page = f'''<!doctype html><meta charset="utf-8"><title>Editor's Cut {y}</title>
<style>
:root{{--bg:#0e0e10;--card:#17181c;--ink:#ecebe8;--mut:#9a9aa3;--acc:#e9b44c}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,sans-serif}}
.wrap{{max-width:1500px;margin:0 auto;padding:28px 18px 90px}} h1{{font-size:34px;margin:0}} h2{{margin:44px 0 10px;font-size:22px}}
h3{{margin:0 0 2px;font-size:18px}} h3 span{{color:var(--mut);font-weight:400;font-size:13px;margin-left:8px}} .sub{{color:var(--mut);margin:2px 0 12px}}
.notes li{{margin:6px 0}} .stats{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}} .stat{{background:var(--card);padding:10px 14px;border-radius:10px}} .stat b{{display:block;font-size:22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}} .grid.small{{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
.grid.small figcaption .c,.grid.small figcaption .m{{display:none}}
figure{{margin:0;background:var(--card);border-radius:8px;overflow:hidden}} img{{width:100%;display:block;aspect-ratio:3/2;object-fit:cover;background:#000}}
figure.big img{{aspect-ratio:auto;max-height:520px;object-fit:contain}} figcaption{{padding:7px 9px 9px;font-size:13px}} .c{{color:#cfcfcf;margin-top:2px}}
.m{{color:var(--mut);font-size:11px;margin-top:3px}} .n{{color:var(--acc);font-size:12px;margin-top:4px}} .num{{float:right;color:var(--acc)}}
.series{{background:#121316;border-radius:12px;padding:16px;margin:16px 0}} .sgrid{{display:grid;grid-template-columns:minmax(300px,1.3fr) 2fr;gap:12px;align-items:start}}
@media(max-width:800px){{.sgrid{{grid-template-columns:1fr}}}}
</style><div class="wrap">
<h1>Editor's Cut — {y}</h1>
<p class="sub">Model pre-selection (top ~6%) → 32 editor agents → 32 adversarial critics ({len(ed['corrections'])} corrections) → curator. Private photos are excluded from this page and from every public set.</p>
<div class="stats"><div class="stat"><b>{len(portfolio)}</b>portfolio</div><div class="stat"><b>{backstock}</b>backstock</div>
<div class="stat"><b>{len(ed['series'])}</b>series</div><div class="stat"><b>{len(ed['instagram_queue'])}</b>Instagram queue</div></div>
<h2>Curator's notes</h2><ul class="notes">{"".join(f"<li>{html.escape(n)}</li>" for n in ed['notes'])}</ul>
<h2>Portfolio picks</h2><p class="sub">Sorted by editor score. ✎ = suggested treatment (applied by the plugin to a "Rescue edit" virtual copy; your original edit is untouched).</p>
<div class="grid">{"".join(tile(p, big=True) for p in portfolio)}</div>
<h2>Series</h2>{series_html}
<h2>Instagram queue</h2><p class="sub">Posting order, sequenced for variety.</p><div class="grid">{ig}</div>
</div>'''
    out = WORK / f'editors_cut_{y}.html'
    out.write_text(page)
    print('wrote', out)


if __name__ == '__main__':
    main()
