"""Review page for keepers that may not be the owner's photos (downloads, other photographers, screenshots).
Writes WORK/authorship.html. Marks are applied through the local helper (serve.py) to not_mine.json.
"""
import collections
import json
import re
from concurrent.futures import ThreadPoolExecutor

from config import WORK, final_portfolio, thumb_path, work_db
from library_page import SMALL, make_small
from rank_prepare import keepers

CAMNAME = re.compile(r'^(IMG|DSC|_MG|MVI|PXL|DSCF|DSCN|P\d{3}|785C|5J5A|3R8A|GQ1A|0V1A|_DSC|IMG-|PANO|BURST|VID|MVIMG'
                     r'|\d{8}_\d{6}|DJI|GOPR|G\d{7})', re.I)
WEB = re.compile(r'tumblr|crestock|shutterstock|istock|getty|stock|twitter|instagram|facebook|pinterest|download|'
                 r'wallpaper|screenshot|_\d{3,4}\.(jpg|png)$|\d{3,4}x\d{3,4}|by[-_ ]', re.I)


def signals(cam, fmt, path, w, h, owned):
    name = path.rsplit('/', 1)[-1]
    out = []
    if fmt not in ('RAW', 'DNG') and cam not in owned:
        out.append(f'camera you never shot RAW with: {cam or "none"}')
    if fmt != 'RAW' and not CAMNAME.match(name):
        out.append('web-style filename')
    if WEB.search(path):
        out.append('web/app folder or name')
    if fmt in ('JPG', 'PNG') and w and h and max(w, h) < 1600:
        out.append(f'small ({int(max(w, h))}px)')
    return out


def main():
    keep, _ = keepers()
    db = work_db()
    rows = db.execute('SELECT image_id, camera, format, path, width, height, substr(capture_time,1,10) FROM images').fetchall()
    raw = collections.Counter(cam for _, cam, fmt, *_ in rows if fmt in ('RAW', 'DNG') and cam)
    owned = {m for m, n in raw.items() if n >= 20}
    final = final_portfolio() or {}
    book, port = set(final.get('book', [])), set(final.get('portfolio', []))
    items = []
    for iid, cam, fmt, path, w, h, d in rows:
        if iid not in keep:
            continue
        sig = signals(cam, fmt, path, w, h, owned)
        if len(sig) < 2:
            continue
        p = keep[iid]
        items.append({'i': iid, 'f': path.rsplit('/', 1)[-1], 'dir': path.rsplit('/', 1)[0].replace('/Volumes/', ''),
                      'd': d or '', 's': sig, 't': p.get('title', ''),
                      'tier': 'book' if iid in book else 'portfolio' if iid in port else p['verdict']})
    rank = {'book': 0, 'portfolio': 1, 'backstock': 2}
    groups = collections.defaultdict(list)
    for x in items:
        groups[x['dir']].append(x)
    ordered = sorted(groups.items(), key=lambda kv: (min(rank.get(x['tier'], 3) for x in kv[1]), -len(kv[1])))
    print(f'{len(items):,} suspects in {len(groups)} folders; making thumbnails...', flush=True)
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(make_small, [x['i'] for x in items]))
    data = {'groups': [{'dir': k, 'items': sorted(v, key=lambda x: (rank.get(x['tier'], 3), x['d']))} for k, v in ordered],
            'owned': sorted(owned), 'small': 'file://' + str(SMALL).replace(' ', '%20'),
            'big': 'file://' + str(thumb_path(0).parent.parent).replace(' ', '%20')}
    out = WORK / 'authorship.html'
    out.write_text(PAGE.replace('__DATA__', json.dumps(data, separators=(',', ':')).replace('</', '<\\/')))
    print(f'wrote {out}')


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorship Review</title>
<style>
:root{--bg:#0d0d0f;--panel:#151619;--line:#24262b;--ink:#ecebe7;--mut:#8e9099;--acc:#e8b44b;--bad:#ef7b7b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(13,13,15,.95);border-bottom:1px solid var(--line)}
.bar{max-width:1600px;margin:0 auto;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0 6px 0 0}a{color:var(--acc)}
button{background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:6px 11px;border-radius:99px;font:inherit;cursor:pointer}
.mut{color:var(--mut)}main{max-width:1600px;margin:0 auto;padding:14px 16px 80px}
.grp{border-top:1px solid var(--line);padding:14px 0}.grp .h{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.grp .h code{font-size:12px;color:#cfcfcf}.grp .h button{padding:2px 9px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.ph{position:relative;background:#000;border-radius:6px;overflow:hidden;aspect-ratio:3/2;cursor:zoom-in}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph .tier{position:absolute;left:5px;top:5px;font-size:10px;background:rgba(0,0,0,.7);padding:1px 6px;border-radius:99px;color:var(--acc)}
.ph label{position:absolute;right:5px;bottom:5px;font-size:11px;background:rgba(0,0,0,.75);padding:2px 7px;border-radius:99px;cursor:pointer;display:flex;gap:4px;align-items:center}
.ph.marked{outline:2px solid var(--bad)}.ph.marked img{opacity:.5}
.lb{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.94);display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:zoom-out;gap:8px}
.lb img{max-width:96vw;max-height:86vh}.lb div{color:var(--mut);font-size:12px;text-align:center;max-width:90vw}
</style></head><body>
<header><div class="bar"><h1>Authorship Review</h1><a href="library.html">← Library</a><a href="private.html">Private review</a>
<span class="mut" id="cnt"></span><button id="apply" style="border-color:#ef7b7b;color:#ef7b7b">Apply “not mine”</button><span class="mut" id="st"></span></div></header>
<main><p class="mut">These keepers look like they may not be your photos: a camera you never shot RAW with, a web-style filename, a small web-sized file, or a web/app folder. Mark the ones that are not yours (per photo or per folder) and press <b>Apply</b>. They will be removed from the portfolio, Book, rankings, Instagram queue and licensing, and gathered in a <code>Rescue/Not Mine</code> collection in Lightroom for you to delete or move. Unmarked photos stay as they are. Your cameras (shot RAW): <span id="owned"></span>.</p><div id="list"></div></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const pad=i=>String(Math.floor(i/1000)).padStart(5,'0');
const sm=i=>`${D.small}/${pad(i)}/${i}.jpg`, big=i=>`${D.big}/${pad(i)}/${i}.jpg`;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let marked=new Set();try{marked=new Set(JSON.parse(localStorage.getItem('rescue-notmine')||'[]'))}catch(e){}
const save=()=>{try{localStorage.setItem('rescue-notmine',JSON.stringify([...marked]))}catch(e){}};
document.getElementById('owned').textContent=D.owned.join(', ');
const list=document.getElementById('list'), cnt=document.getElementById('cnt'), st=document.getElementById('st');
const upd=()=>{cnt.textContent=`${D.groups.reduce((n,g)=>n+g.items.length,0)} suspects · ${marked.size} marked not mine`};
list.innerHTML=D.groups.map((g,k)=>`<section class="grp"><div class="h"><code>${esc(g.dir)}</code><span class="mut">${g.items.length} photos</span><button data-g="${k}" data-on="1">mark folder not mine</button><button data-g="${k}" data-on="0">unmark folder</button></div><div class="grid">${g.items.map(x=>`<div class="ph${marked.has(x.i)?' marked':''}" data-i="${x.i}"><img loading="lazy" src="${sm(x.i)}" alt=""><span class="tier">${x.tier}</span><label><input type="checkbox" ${marked.has(x.i)?'checked':''}> not mine</label></div>`).join('')}</div></section>`).join('');
const byId=new Map(D.groups.flatMap(g=>g.items.map(x=>[x.i,x])));
list.addEventListener('change',e=>{const ph=e.target.closest('.ph');const i=+ph.dataset.i;e.target.checked?marked.add(i):marked.delete(i);ph.classList.toggle('marked',e.target.checked);save();upd()});
list.addEventListener('click',e=>{const b=e.target.closest('button[data-g]');if(b){const on=b.dataset.on==='1';const sec=b.closest('section');
  D.groups[+b.dataset.g].items.forEach(x=>on?marked.add(x.i):marked.delete(x.i));sec.querySelectorAll('.ph').forEach(ph=>{ph.classList.toggle('marked',on);ph.querySelector('input').checked=on});save();upd();return}
 if(e.target.closest('label'))return;const ph=e.target.closest('.ph');if(!ph)return;const x=byId.get(+ph.dataset.i);
 const lb=document.createElement('div');lb.className='lb';lb.innerHTML=`<img src="${big(x.i)}"><div><b>${esc(x.t)}</b> · ${esc(x.f)} · ${esc(x.d)} · ${x.tier}<br>${x.s.map(esc).join(' · ')}</div>`;lb.onclick=()=>lb.remove();document.body.appendChild(lb)});
const API='http://127.0.0.1:8765/api';
document.getElementById('apply').onclick=async()=>{if(!marked.size){st.textContent='Nothing marked yet.';return}st.textContent=`Applying ${marked.size}…`;
 try{const r=await fetch(API+'/not-mine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[...marked]})});const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);
  st.textContent=`Applied (${j.total} not-mine in total). Rebuilding…`;
  const poll=async()=>{try{const s=await (await fetch(API+'/status')).json();if(s.building){setTimeout(poll,3000);return}if(s.error){st.textContent='Rebuild failed: '+s.error;return}marked.clear();save();location.reload()}catch(e){setTimeout(poll,4000)}};setTimeout(poll,3000)}
 catch(e){st.textContent='The local helper is not running. Ask Claude to start it.'}};
upd();
</script></body></html>'''


if __name__ == '__main__':
    main()
