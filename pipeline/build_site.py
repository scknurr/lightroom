"""Static portfolio website generated from the final portfolio.

Output: /Users/Steve/DEV/steve-knurr-photo/ (self-contained; open index.html or host anywhere).
Excludes private photos, photos that aren't the owner's, and photos with minors (listed in a report).
"""
import html
import json
import shutil
from pathlib import Path

from PIL import Image

from config import WORK, final_portfolio, thumb_path
from rank_prepare import keepers

SITE = Path('/Users/Steve/DEV/steve-knurr-photo')
NAME = 'Steve Knurr'
MID = 760
# Personal work first, commercial and event work last.
THEME_ORDER = ['Protest and Civic Action', 'Dogs', 'Snow Dogs', 'Bike Racing', 'Portrait Sessions and Fashion',
               'Weather and Sky', 'Madison and the Capitol', 'Weddings and Engagements', 'Cycling Life, Rides and Gear',
               'Lakes, Skies and Sailing', 'Winter in Wisconsin', 'Travel and Road Trips', 'Outdoor Adventure and Camping',
               'Wildlife and Birds', 'Autumn Color', 'Flowers and Botanicals', 'Rural Wisconsin and Americana',
               'Streets, Signs and Graffiti', 'Still Life, Toys and Curiosities', 'Friends, Parties and Gatherings',
               'Holidays and Celebrations', 'Yoga and Fitness', 'Makers and Craft at Work', 'Food and Drink',
               'Brand, Product and Commercial Work', 'Corporate, Office and Conference Events']


def slug(s):
    return ''.join(ch.lower() if ch.isalnum() else '-' for ch in s).strip('-').replace('--', '-')


def export(iid):
    big, mid = SITE / 'img' / f'{iid}.jpg', SITE / 'img' / f'{iid}-m.jpg'
    if not big.exists():
        shutil.copyfile(thumb_path(iid), big)
    if not mid.exists():
        im = Image.open(big).convert('RGB')
        im.thumbnail((MID, MID), Image.LANCZOS)
        im.save(mid, 'JPEG', quality=82, optimize=True, progressive=True)
    with Image.open(big) as im:
        return im.size


def main():
    final = final_portfolio()
    keep, _ = keepers()
    (SITE / 'img').mkdir(parents=True, exist_ok=True)
    skipped_minors = []

    def ok(i):
        p = keep.get(i)
        if not p:
            return False
        if p.get('minors'):
            skipped_minors.append(i)
            return False
        return True

    meta = {}

    def info(i):
        if i not in meta:
            p = keep[i]
            w, h = export(i)
            meta[i] = {'id': i, 't': p.get('title', ''), 'c': p.get('caption', ''), 'w': w, 'h': h}
        return meta[i]

    # public section titles and intros (WORK/site_copy/intros.json); the editors' internal notes are never published
    f = WORK / 'site_copy' / 'intros.json'
    intros = {x['theme']: x for x in json.loads(f.read_text())['sections']} if f.exists() else {}
    book = [info(i) for i in final['book'] if ok(i)]
    themes = []
    order = {n: k for k, n in enumerate(THEME_ORDER)}
    for t, ids in sorted(final['theme_order'].items(), key=lambda kv: (order.get(kv[0], 50), -len(kv[1]))):
        if t == 'Unsorted':
            continue
        photos = [info(i) for i in ids if ok(i)]
        if len(photos) < 3:
            continue
        pub = intros.get(t, {})
        themes.append({'name': pub.get('title') or t, 'slug': slug(t), 'note': pub.get('intro', ''), 'photos': photos})

    nav = lambda active: (f'<header class="top"><a class="brand" href="index.html">{NAME}</a><nav>'
                          f'<a href="index.html"{" class=on" if active == "home" else ""}>Selected</a>'
                          f'<a href="work.html"{" class=on" if active == "work" else ""}>Work</a>'
                          f'<a href="about.html"{" class=on" if active == "about" else ""}>About</a></nav></header>')

    def page(title, active, body, data=None):
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{html.escape(title)}</title><meta name="description" content="Photography by {NAME}: documentary, portraits, dogs, bike racing and Wisconsin.">'
                f'<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,500&family=Inter:wght@400;500&display=swap" rel="stylesheet">'
                f'<link rel="stylesheet" href="site.css"></head><body>{nav(active)}<main>{body}</main>'
                f'<footer>© {NAME}. All photographs by {NAME}; please ask before using them.</footer>'
                + (f'<script id="data" type="application/json">{json.dumps(data, separators=(",", ":"))}</script><script src="site.js"></script>' if data else '')
                + '</body></html>')

    def fig(p, cls=''):
        ratio = p['w'] / p['h']
        return (f'<figure class="ph {cls}" data-id="{p["id"]}" style="--r:{ratio:.4f}"><img loading="lazy" decoding="async" '
                f'src="img/{p["id"]}-m.jpg" srcset="img/{p["id"]}-m.jpg {MID}w, img/{p["id"]}.jpg 1440w" sizes="(max-width: 800px) 100vw, 50vw" '
                f'alt="{html.escape(p["c"] or p["t"])}"><figcaption>{html.escape(p["t"])}</figcaption></figure>')

    # Home: hero = the first Book frame that holds up full-bleed (horizontal, full resolution)
    hero = next((p for p in book if p['w'] >= 1400 and p['w'] > p['h'] * 1.3), book[0])
    spreads = [f'<div class="spread one">{fig(hero, "hero-pic")}</div>']
    rest = book[1:]
    for k in range(0, len(rest), 2):
        pair = rest[k:k + 2]
        spreads.append(f'<div class="spread {"two" if len(pair) == 2 else "one"}">' + ''.join(fig(p) for p in pair) + '</div>')
    home = (f'<section class="hero" style="background-image:url(img/{hero["id"]}.jpg)"><div class="hero-in"><h1>{NAME}</h1>'
            f'<p>Twenty years of photographs from Wisconsin and beyond: dogs, bike racing, protest and civic life, portraits and weather.</p>'
            f'<a class="cta" href="#selected">Selected work ↓</a></div></section>'
            f'<section id="selected" class="book"><h2>Selected work</h2><p class="lede">{len(book)} photographs, sequenced as a book.</p>{"".join(spreads)}'
            f'<p class="more"><a href="work.html">See the full portfolio by subject →</a></p></section>')
    (SITE / 'index.html').write_text(page(f'{NAME} — Photography', 'home', home, {'photos': book}))

    # Work index
    tiles = ''.join(f'<a class="tile" href="{t["slug"]}.html"><img loading="lazy" src="img/{t["photos"][0]["id"]}-m.jpg" alt="">'
                    f'<span><b>{html.escape(t["name"])}</b><i>{len(t["photos"])} photographs</i></span></a>' for t in themes)
    (SITE / 'work.html').write_text(page(f'Work — {NAME}', 'work', f'<section class="work"><h2>Work</h2><div class="tiles">{tiles}</div></section>'))

    # Theme pages
    for k, t in enumerate(themes):
        prev_t, next_t = themes[k - 1], themes[(k + 1) % len(themes)]
        body = (f'<section class="theme"><p class="crumb"><a href="work.html">Work</a></p><h2>{html.escape(t["name"])}</h2>'
                f'<p class="lede">{html.escape(t["note"])}</p><div class="masonry">{"".join(fig(p) for p in t["photos"])}</div>'
                f'<p class="pager"><a href="{prev_t["slug"]}.html">← {html.escape(prev_t["name"])}</a><a href="{next_t["slug"]}.html">{html.escape(next_t["name"])} →</a></p></section>')
        (SITE / f'{t["slug"]}.html').write_text(page(f'{t["name"]} — {NAME}', 'work', body, {'photos': t['photos']}))

    about = ('<section class="about"><h2>About</h2><p class="edit-me">Write a few lines about yourself and your work here: '
             'where you are based, what you photograph, and how to reach you for commissions or licensing.</p>'
             '<p class="edit-me">Contact: add your email or a contact link.</p></section>')
    (SITE / 'about.html').write_text(page(f'About — {NAME}', 'about', about))
    (SITE / 'site.css').write_text(CSS)
    (SITE / 'site.js').write_text(JS)
    # drop images from earlier builds that are no longer published (removed, private or not-mine photos)
    live = {str(i) for i in meta}
    for f in (SITE / 'img').glob('*.jpg'):
        if f.stem.split('-')[0] not in live:
            f.unlink()
    report = {'book': len(book), 'themes': {t['name']: len(t['photos']) for t in themes},
              'excluded_minors': sorted(set(skipped_minors)), 'images': len(meta)}
    (SITE / 'build-report.json').write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != 'excluded_minors'}, indent=1), '| minors excluded:', len(set(skipped_minors)))


CSS = r''':root{--bg:#0f0f0f;--ink:#efede8;--mut:#9a9893;--line:#262626;--acc:#e3b45a}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Inter,system-ui,-apple-system,sans-serif}
a{color:inherit}h1,h2{font-family:Fraunces,Georgia,serif;font-weight:300;letter-spacing:-.01em;margin:0}
.top{position:sticky;top:0;z-index:10;display:flex;flex-wrap:wrap;gap:6px 16px;justify-content:space-between;align-items:center;padding:18px clamp(16px,4vw,48px);background:linear-gradient(var(--bg),rgba(15,15,15,.85));backdrop-filter:blur(6px)}
.brand{font-family:Fraunces,Georgia,serif;font-size:20px;text-decoration:none}nav{display:flex;gap:18px}nav a{text-decoration:none;color:var(--mut);font-size:14px;letter-spacing:.04em;text-transform:uppercase}nav a.on,nav a:hover{color:var(--ink)}
main{padding:0 clamp(16px,4vw,48px) 60px}
body{overflow-x:hidden}.hero{margin:0 calc(-1*clamp(16px,4vw,48px));height:92vh;min-height:520px;background-size:cover;background-position:center;display:flex;align-items:flex-end}
.hero-in{padding:clamp(20px,6vw,80px);background:linear-gradient(transparent,rgba(0,0,0,.55) 35%,rgba(0,0,0,.85));width:100%;text-shadow:0 1px 18px rgba(0,0,0,.55)}
.hero h1{font-size:clamp(44px,8vw,104px);line-height:1}.hero p{max-width:640px;color:#ddd;font-size:clamp(16px,1.6vw,20px)}
.cta{display:inline-block;margin-top:8px;color:var(--acc);text-decoration:none;font-size:14px;letter-spacing:.06em;text-transform:uppercase}
.book,.work,.theme,.about{max-width:1500px;margin:0 auto}.book h2,.work h2,.theme h2,.about h2{font-size:clamp(32px,4vw,52px);margin:64px 0 8px}
.lede{color:var(--mut);max-width:760px;margin:0 0 36px}
.spread{display:grid;gap:clamp(12px,2vw,28px);margin:0 0 clamp(36px,6vw,96px);align-items:center}
.spread.two{grid-template-columns:1fr 1fr}.spread.one{grid-template-columns:minmax(0,1100px);justify-content:center}
@media(max-width:800px){.spread.two{grid-template-columns:1fr}.hero{height:78vh}}
@media(max-width:480px){.brand{font-size:18px}nav{gap:14px}nav a{font-size:12px}}
.ph{margin:0;cursor:zoom-in}.ph img{width:100%;height:auto;display:block;aspect-ratio:var(--r);background:#1a1a1a}
.ph figcaption{color:var(--mut);font-size:13px;margin-top:8px}
.more{text-align:center;margin:40px 0}.more a,.crumb a,.pager a{color:var(--acc);text-decoration:none}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}
.tile{position:relative;display:block;aspect-ratio:4/3;overflow:hidden;border-radius:4px;text-decoration:none}
.tile img{width:100%;height:100%;object-fit:cover;transition:transform .5s}.tile:hover img{transform:scale(1.04)}
.tile span{position:absolute;inset:auto 0 0 0;padding:40px 16px 14px;background:linear-gradient(transparent,rgba(0,0,0,.8))}
.tile b{display:block;font-family:Fraunces,Georgia,serif;font-weight:500;font-size:20px}.tile i{font-style:normal;color:#ccc;font-size:13px}
.masonry{columns:3 320px;column-gap:18px}.masonry .ph{break-inside:avoid;margin-bottom:22px}
.crumb{margin:48px 0 0;font-size:14px}.pager{display:flex;justify-content:space-between;margin-top:40px}
.about p{max-width:680px}.edit-me{border-left:3px solid var(--acc);padding-left:14px;color:var(--mut)}
footer{text-align:center;color:var(--mut);font-size:13px;padding:30px 16px 50px;border-top:1px solid var(--line)}
.lb{position:fixed;inset:0;z-index:50;background:rgba(8,8,8,.97);display:flex;flex-direction:column;align-items:center;justify-content:center}
.lb img{max-width:94vw;max-height:84vh;object-fit:contain}.lb p{color:#ccc;margin:14px 20px 0;text-align:center;max-width:800px;font-size:14px}
.lb button{position:absolute;background:none;border:0;color:#fff;font-size:34px;cursor:pointer;padding:16px;opacity:.75}.lb button:hover{opacity:1}
.lb .x{top:6px;right:10px}.lb .pv{left:4px;top:50%;transform:translateY(-50%)}.lb .nx{right:4px;top:50%;transform:translateY(-50%)}
'''

JS = r'''(()=>{const D=JSON.parse(document.getElementById('data').textContent),P=D.photos,idx=new Map(P.map((p,k)=>[p.id,k]));let k=0,lb;
const esc=s=>String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(){const p=P[k];lb.querySelector('img').src=`img/${p.id}.jpg`;lb.querySelector('img').alt=p.c||p.t;lb.querySelector('p').innerHTML=`<b>${esc(p.t)}</b>${p.c?' — '+esc(p.c):''}`}
function open(i){k=i;lb=document.createElement('div');lb.className='lb';lb.innerHTML='<img alt=""><p></p><button class="x" aria-label="Close">×</button><button class="pv" aria-label="Previous">‹</button><button class="nx" aria-label="Next">›</button>';
 document.body.appendChild(lb);document.body.style.overflow='hidden';show();
 lb.onclick=e=>{if(e.target.classList.contains('pv'))go(-1);else if(e.target.classList.contains('nx'))go(1);else if(e.target===lb||e.target.classList.contains('x'))close()};
 let x0=null;lb.ontouchstart=e=>x0=e.touches[0].clientX;lb.ontouchend=e=>{if(x0===null)return;const dx=e.changedTouches[0].clientX-x0;if(Math.abs(dx)>40)go(dx<0?1:-1);x0=null}}
function go(d){k=(k+d+P.length)%P.length;show()}
function close(){lb.remove();lb=null;document.body.style.overflow=''}
addEventListener('keydown',e=>{if(!lb)return;if(e.key==='Escape')close();if(e.key==='ArrowRight')go(1);if(e.key==='ArrowLeft')go(-1)});
document.addEventListener('click',e=>{const f=e.target.closest('.ph');if(f&&!lb&&idx.has(+f.dataset.id))open(idx.get(+f.dataset.id))});})();
'''


if __name__ == '__main__':
    main()
