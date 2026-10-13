"""One local page for the whole reviewed library: Themes -> Series -> photos, plus Portfolio, Instagram queue,
By year and search. Private photos and editor skips are excluded. Writes WORK/library.html.
"""
import html
import json
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from config import WORK, thumb_path, work_db

SMALL = WORK / 'thumbs_small'
SMALL_LONG = 480


def small_path(iid):
    return SMALL / f'{iid // 1000:05d}' / f'{iid}.jpg'


def make_small(iid):
    out = small_path(iid)
    if out.exists():
        return
    try:
        im = Image.open(thumb_path(iid))
        im.draft('RGB', (SMALL_LONG * 2, SMALL_LONG * 2))
        im = im.convert('RGB')
        im.thumbnail((SMALL_LONG, SMALL_LONG), Image.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, 'JPEG', quality=80)
    except Exception:
        pass


def main():
    db = work_db()
    meta = {iid: (y or '', d or '', bool(pv)) for iid, y, d, pv in db.execute(
        '''SELECT i.image_id, i.year, substr(i.capture_time,1,10), s.private
           FROM images i LEFT JOIN selection s USING(image_id)''')}
    recs = {}
    for f in sorted(WORK.glob('editor_*.json')):
        for p in json.loads(f.read_text())['photos']:
            recs[p['image_id']] = p
    photos, hidden_private = {}, 0
    for iid, p in recs.items():
        if p['verdict'] == 'skip':
            continue
        y, d, priv = meta.get(iid, ('', '', False))
        if p.get('private') or priv:
            hidden_private += 1
            continue
        photos[iid] = {
            'i': iid, 'v': 'P' if p['verdict'] == 'portfolio' else 'B', 's': p.get('editor_score', 0),
            't': p.get('title', ''), 'c': p.get('caption', ''), 'k': p.get('keywords', [])[:12],
            'y': y, 'd': d, 'u': p.get('uses', []), 'r': p.get('release_notes', []),
            'p': p.get('people', ''), 'm': bool(p.get('minors')),
            'n': (p.get('treatment') or {}).get('notes', ''), 'w': p.get('why', ''),
        }

    cur = json.loads((WORK / 'curation_library.json').read_text())
    series = {}
    for s in cur['series']:
        members = [i for i in s['members'] if i in photos]
        if members:
            series[s['name']] = {'name': s['name'], 'desc': s['description'], 'year': s.get('year', ''),
                                 'cover': s['cover_id'] if s['cover_id'] in photos else members[0],
                                 'members': members, 'best': s.get('best_for', [])}
    n_themes = {}
    for c in cur['collections']:
        for n in c['series']:
            n_themes[n] = n_themes.get(n, 0) + 1
    themes = []
    for c in cur['collections']:
        ss = [series[n] for n in c['series'] if n in series]
        if not ss:
            continue
        ss.sort(key=lambda s: s['year'])
        n_photos = len({i for s in ss for i in s['members']})
        n_port = len({i for s in ss for i in s['members'] if photos[i]['v'] == 'P'})
        # Prefer covers from series that belong only to this theme, so a shared series can't hijack it.
        best = min(ss, key=lambda s: (n_themes.get(s['name'], 1), -photos[s['cover']]['s'], -len(s['members'])))
        cover = best['cover']
        themes.append({'name': c['name'], 'desc': c['description'], 'series': [s['name'] for s in ss],
                       'n': n_photos, 'np': n_port, 'cover': cover})
    themes.sort(key=lambda t: -t['np'])
    in_series = {i for s in series.values() for i in s['members']}
    ig = [i for i in cur.get('instagram_queue', []) if i in photos]

    print(f'{len(photos):,} photos on the page ({hidden_private:,} private hidden); making small thumbnails...', flush=True)
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(make_small, photos))

    data = {
        'photos': list(photos.values()), 'series': list(series.values()), 'themes': themes, 'ig': ig,
        'notes': cur.get('notes', []), 'unsorted': [i for i in photos if i not in in_series],
        'stats': {'photos': len(photos), 'portfolio': sum(p['v'] == 'P' for p in photos.values()),
                  'series': len(series), 'themes': len(themes), 'private_hidden': hidden_private},
        'small': 'file://' + str(SMALL).replace(' ', '%20'), 'big': 'file://' + str(thumb_path(0).parent.parent).replace(' ', '%20'),
    }
    page = PAGE.replace('__DATA__', json.dumps(data, separators=(',', ':')).replace('</', '<\\/'))
    out = WORK / 'library.html'
    out.write_text(page)
    print(f'wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {len(themes)} themes, {len(series)} series, '
          f'{data["stats"]["portfolio"]:,} portfolio, {len(ig)} in Instagram queue')


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rescue Library</title>
<style>
:root{--bg:#0d0d0f;--panel:#151619;--line:#24262b;--ink:#ecebe7;--mut:#8e9099;--acc:#e8b44b;--port:#e8b44b;--back:#5d6370}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(13,13,15,.94);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar{max-width:1600px;margin:0 auto;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0 8px 0 0;letter-spacing:.2px}
nav button{background:none;border:1px solid transparent;color:var(--mut);padding:6px 11px;border-radius:99px;cursor:pointer;font:inherit}
nav button.on{color:var(--ink);border-color:var(--line);background:var(--panel)}
input[type=search]{flex:1;min-width:180px;max-width:420px;background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:7px 12px;border-radius:99px;font:inherit}
label.tog{color:var(--mut);font-size:13px;display:flex;gap:6px;align-items:center;cursor:pointer}
main{max-width:1600px;margin:0 auto;padding:18px 16px 80px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:4px 0 14px}.stat{background:var(--panel);border:1px solid var(--line);padding:9px 13px;border-radius:10px}.stat b{display:block;font-size:20px}
.notes{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:6px 18px;margin-bottom:18px}.notes li{margin:7px 0;color:#d6d4cf}
.tgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.tcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer}
.tcard img{width:100%;aspect-ratio:3/2;object-fit:cover;display:block;background:#000}.tcard div{padding:9px 11px}.tcard b{font-size:15px}.tcard span{color:var(--mut);font-size:12px;display:block}
.crumbs{margin:2px 0 12px;color:var(--mut)}.crumbs a{color:var(--acc);cursor:pointer;text-decoration:none}
h2{font-size:22px;margin:6px 0 4px}h3{font-size:16px;margin:0}.desc{color:var(--mut);margin:2px 0 12px}
.series{border-top:1px solid var(--line);padding:16px 0}.series .head{display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap}
.series .meta{color:var(--mut);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin-top:10px}
.ph{position:relative;background:#000;border-radius:6px;overflow:hidden;cursor:zoom-in;aspect-ratio:3/2}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph .badge{position:absolute;left:6px;top:6px;font-size:10px;padding:1px 6px;border-radius:99px;background:rgba(0,0,0,.65);color:var(--port)}
.ph .cap{position:absolute;inset:auto 0 0 0;padding:16px 7px 5px;font-size:11px;background:linear-gradient(transparent,rgba(0,0,0,.8));opacity:0;transition:.15s}
.ph:hover .cap{opacity:1}
.more{margin-top:10px;background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:6px 12px;border-radius:99px;cursor:pointer;font:inherit}
.lb{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.92);display:grid;grid-template-columns:1fr minmax(260px,360px)}
.lb img{max-width:100%;max-height:100vh;object-fit:contain;margin:auto;display:block;align-self:center}
.lb aside{background:var(--panel);padding:20px;overflow:auto;border-left:1px solid var(--line)}.lb aside h3{font-size:18px;margin-bottom:6px}
.lb .kw span{display:inline-block;background:#202227;border-radius:99px;padding:2px 9px;margin:3px 4px 0 0;font-size:12px}
.lb .row{color:var(--mut);font-size:12px;margin-top:10px}.lb .row b{color:var(--ink);font-weight:500}
.lb .x{position:absolute;right:376px;top:12px;font-size:26px;color:#fff;cursor:pointer;background:none;border:0}
@media (max-width:760px){.lb{grid-template-columns:1fr}.lb aside{max-height:40vh}.lb .x{right:12px}}
.empty{color:var(--mut);padding:30px 0}
</style></head><body>
<header><div class="bar"><h1>Rescue Library</h1>
<nav><button data-v="themes" class="on">Themes</button><button data-v="portfolio">Portfolio</button><button data-v="ig">Instagram queue</button><button data-v="years">By year</button></nav>
<input type="search" id="q" placeholder="Search titles, captions, keywords…">
<label class="tog"><input type="checkbox" id="pOnly"> Portfolio only</label></div></header>
<main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const P=new Map(D.photos.map(p=>[p.i,p])), S=new Map(D.series.map(s=>[s.name,s]));
const pad=i=>String(Math.floor(i/1000)).padStart(5,'0');
const sm=i=>`${D.small}/${pad(i)}/${i}.jpg`, big=i=>`${D.big}/${pad(i)}/${i}.jpg`;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const main=document.getElementById('main'), q=document.getElementById('q'), pOnly=document.getElementById('pOnly');
let view='themes', theme=null, lastIds=[];
const sortP=ids=>ids.slice().sort((a,b)=>(P.get(a).v===P.get(b).v?P.get(b).s-P.get(a).s:P.get(a).v==='P'?-1:1));
function tile(i,num){const p=P.get(i);return `<div class="ph" data-i="${i}"><img loading="lazy" src="${sm(i)}" alt="">${p.v==='P'?'<span class="badge">portfolio</span>':''}${num?`<span class="badge" style="left:auto;right:6px">${num}</span>`:''}<div class="cap">${esc(p.t)}</div></div>`}
function grid(ids,limit){const pick=pOnly.checked?ids.filter(i=>P.get(i).v==='P'):ids;const show=limit?pick.slice(0,limit):pick;
 return `<div class="grid">${show.map(i=>tile(i)).join('')}</div>`+(limit&&pick.length>limit?`<button class="more" data-more>Show all ${pick.length}</button>`:'')}
function stats(){const s=D.stats;return `<div class="stats"><div class="stat"><b>${s.photos.toLocaleString()}</b>reviewed keepers</div><div class="stat"><b>${s.portfolio.toLocaleString()}</b>portfolio</div><div class="stat"><b>${s.themes}</b>themes</div><div class="stat"><b>${s.series}</b>series</div><div class="stat"><b>${D.ig.length}</b>Instagram queue</div><div class="stat"><b>${s.private_hidden.toLocaleString()}</b>private (hidden)</div></div>`}
function renderThemes(){main.innerHTML=stats()+`<ul class="notes">${D.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul><div class="tgrid">${D.themes.map((t,k)=>`<div class="tcard" data-t="${k}"><img loading="lazy" src="${sm(t.cover)}"><div><b>${esc(t.name)}</b><span>${t.series.length} series · ${t.n.toLocaleString()} photos · ${t.np.toLocaleString()} portfolio</span></div></div>`).join('')}</div>`}
function renderTheme(k){const t=D.themes[k];main.innerHTML=`<div class="crumbs"><a data-back>All themes</a> / ${esc(t.name)}</div><h2>${esc(t.name)}</h2><p class="desc">${esc(t.desc)}</p>`+
 t.series.map(n=>{const s=S.get(n);const ids=sortP(s.members);const np=ids.filter(i=>P.get(i).v==='P').length;
  return `<section class="series" data-s="${esc(n)}"><div class="head"><h3>${esc(s.name)}</h3><span class="meta">${esc(s.year)} · ${ids.length} photos · ${np} portfolio${s.best.length?' · best for '+esc(s.best.join(', ')):''}</span></div><p class="desc">${esc(s.desc)}</p>${grid(ids,12)}</section>`}).join('');window.scrollTo(0,0)}
function renderList(title,ids,desc,numbered){lastIds=ids;main.innerHTML=`<h2>${title}</h2><p class="desc">${desc}</p>`+(numbered?`<div class="grid">${ids.map((i,n)=>tile(i,n+1)).join('')}</div>`:grid(ids,400));}
function renderYears(){const by={};for(const p of D.photos)(by[p.y||'undated']??=[]).push(p.i);
 main.innerHTML=Object.keys(by).sort().reverse().map(y=>{const ids=sortP(by[y]);return `<section class="series"><div class="head"><h3>${esc(y)}</h3><span class="meta">${ids.length} photos · ${ids.filter(i=>P.get(i).v==='P').length} portfolio</span></div>${grid(ids,24)}</section>`}).join('')}
function renderSearch(term){const t=term.toLowerCase();const ids=sortP(D.photos.filter(p=>(p.t+' '+p.c+' '+p.k.join(' ')+' '+p.y).toLowerCase().includes(t)).map(p=>p.i));
 renderList(`Search: “${esc(term)}”`,ids,`${ids.length} matching photos, portfolio first.`)}
function render(){const term=q.value.trim();if(term)return renderSearch(term);
 if(view==='themes')return theme===null?renderThemes():renderTheme(theme);
 if(view==='portfolio')return renderList('Portfolio',sortP(D.photos.filter(p=>p.v==='P').map(p=>p.i)),'Every portfolio frame in the library, highest editor score first.');
 if(view==='ig')return renderList('Instagram queue',D.ig,'Posting order from the head curator: varied, never two from the same series back-to-back, no minors. Confirm consent for recognizable people before posting.',true);
 if(view==='years')return renderYears()}
document.querySelector('nav').onclick=e=>{const b=e.target.closest('button');if(!b)return;document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));view=b.dataset.v;theme=null;q.value='';render()};
let qt;q.oninput=()=>{clearTimeout(qt);qt=setTimeout(render,200)};pOnly.onchange=render;
main.onclick=e=>{const tc=e.target.closest('.tcard');if(tc){theme=+tc.dataset.t;return render()}
 if(e.target.closest('[data-back]')){theme=null;return render()}
 const m=e.target.closest('[data-more]');if(m){const sec=m.closest('section');const s=sec&&S.get(sec.dataset.s);
  const ids=s?sortP(s.members):lastIds;m.previousElementSibling.outerHTML=grid(ids);m.remove();return}
 const ph=e.target.closest('.ph');if(ph)open(+ph.dataset.i)};
function open(i){const p=P.get(i);const series=D.series.filter(s=>s.members.includes(i)).map(s=>s.name);
 const lb=document.createElement('div');lb.className='lb';lb.innerHTML=`<img src="${big(i)}" alt=""><aside><h3>${esc(p.t)}</h3><div>${esc(p.c)}</div>
 <div class="kw">${p.k.map(k=>`<span>${esc(k)}</span>`).join('')}</div>
 <div class="row"><b>${p.v==='P'?'Portfolio':'Backstock'}</b> · editor ${p.s}/10 · ${esc(p.d||p.y)} · #${i}</div>
 ${p.w?`<div class="row">${esc(p.w)}</div>`:''}${p.n?`<div class="row"><b>Edit:</b> ${esc(p.n)}</div>`:''}
 <div class="row"><b>Uses:</b> ${esc(p.u.join(', ')||'—')}${p.r.length?` · ${esc(p.r.join(', ').replaceAll('_',' '))}`:''}${p.m?' · <b>minors</b>':''}</div>
 ${series.length?`<div class="row"><b>Series:</b> ${esc(series.join(' · '))}</div>`:''}</aside><button class="x" aria-label="Close">×</button>`;
 lb.onclick=e=>{if(e.target===lb||e.target.classList.contains('x'))lb.remove()};document.body.appendChild(lb);
 const key=e=>{if(e.key==='Escape'){lb.remove();removeEventListener('keydown',key)}};addEventListener('keydown',key)}
const h=decodeURIComponent(location.hash.slice(1));if(h.startsWith('t=')){const k=D.themes.findIndex(t=>t.name.toLowerCase().includes(h.slice(2).toLowerCase()));if(k>=0)theme=k}
render();
</script></body></html>'''


if __name__ == '__main__':
    main()
