"""Local review page for every privacy-flagged photo, so the owner can clear false positives.
Writes WORK/private.html. Marks are kept in the browser (localStorage) and listed for copy/paste back.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from config import WORK, cleared_private, thumb_path, work_db
from library_page import SMALL, make_small


def main():
    db = work_db()
    rows = db.execute('''SELECT s.image_id, s.private, c.priv_nudity, c.priv_underwear, c.priv_document,
                                i.year, substr(i.capture_time,1,10), i.path
                         FROM selection s JOIN clip c USING(image_id) JOIN images i USING(image_id)''').fetchall()
    editor = {}
    for f in sorted(WORK.glob('editor_*.json')):
        for p in json.loads(f.read_text())['photos']:
            if p.get('private'):
                editor[p['image_id']] = p
    items = []
    cleared = cleared_private()
    for iid, auto, nud, und, doc, y, d, path in rows:
        if (not auto and iid not in editor) or iid in cleared:
            continue
        why = []
        if iid in editor:
            why.append('editor')
        if auto:
            top = max((nud or 0, 'nudity'), (und or 0, 'underwear'), (doc or 0, 'ID/document'))
            why.append(f'auto: {top[1]} {top[0]:.2f}')
        items.append({'i': iid, 'y': y or '', 'd': d or '', 'w': why, 'f': path.rsplit('/', 1)[-1],
                      'c': round(max(nud or 0, und or 0, doc or 0), 3), 'e': iid in editor})
    items.sort(key=lambda x: (x['d'], x['i']), reverse=True)
    print(f'{len(items):,} flagged photos; making small thumbnails...', flush=True)
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(make_small, [x['i'] for x in items]))
    data = {'items': items, 'small': 'file://' + str(SMALL).replace(' ', '%20'),
            'big': 'file://' + str(thumb_path(0).parent.parent).replace(' ', '%20')}
    out = WORK / 'private.html'
    out.write_text(PAGE.replace('__DATA__', json.dumps(data, separators=(',', ':')).replace('</', '<\\/')))
    print(f'wrote {out}')


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private Review</title>
<style>
:root{--bg:#0d0d0f;--panel:#151619;--line:#24262b;--ink:#ecebe7;--mut:#8e9099;--acc:#e8b44b;--ok:#6fd39a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(13,13,15,.95);border-bottom:1px solid var(--line)}
.bar{max-width:1600px;margin:0 auto;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0 6px 0 0}a{color:var(--acc)}
select,button{background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:6px 11px;border-radius:99px;font:inherit;cursor:pointer}
.count{color:var(--mut)}
main{max-width:1600px;margin:0 auto;padding:14px 16px 80px}
.help{color:var(--mut);margin:0 0 14px}
.day{margin:18px 0 6px;color:var(--mut);font-size:13px;display:flex;gap:10px;align-items:center}
.day button{padding:2px 9px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
.ph{position:relative;background:#000;border-radius:6px;overflow:hidden;aspect-ratio:3/2;cursor:zoom-in}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph .why{position:absolute;left:5px;top:5px;font-size:10px;background:rgba(0,0,0,.7);padding:1px 6px;border-radius:99px;color:#f2c48a}
.ph label{position:absolute;right:5px;bottom:5px;font-size:11px;background:rgba(0,0,0,.75);padding:2px 7px;border-radius:99px;cursor:pointer;display:flex;gap:4px;align-items:center}
.ph.cleared{outline:2px solid var(--ok)}.ph.cleared img{opacity:.55}
#out{display:none;width:100%;min-height:90px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:12px ui-monospace,Menlo,monospace;margin-top:8px}
.lb{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.94);display:flex;align-items:center;justify-content:center;cursor:zoom-out}
.lb img{max-width:96vw;max-height:92vh}.lb div{position:absolute;bottom:12px;left:0;right:0;text-align:center;color:var(--mut);font-size:12px}
</style></head><body>
<header><div class="bar"><h1>Private Review</h1><a href="library.html">← Library</a><a href="ranked.html">Ranked</a>
<select id="f"><option value="all">All flagged</option><option value="editor">Editor-flagged</option><option value="auto">Auto-flagged only</option><option value="low">Auto, lower confidence (&lt; 0.99)</option></select>
<span class="count" id="cnt"></span><button id="apply" style="border-color:#6fd39a;color:#6fd39a">Apply</button><span id="st" class="count"></span><button id="show">Show cleared IDs</button><button id="reset">Reset marks</button></div>
<div class="bar" style="padding-top:0"><textarea id="out" readonly></textarea></div></header>
<main><p class="help">Photos flagged private stay in your library but are kept out of Portfolio, Instagram, licensing and the galleries. Tick <b>not private</b> on any false positive (or use the per-day buttons), then press <b>Apply</b>. Applied photos leave this list and join the library, rankings and Lightroom manifest; the pages rebuild in about a minute and this page reloads itself. Marks are saved in this browser until applied.</p><div id="list"></div></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const pad=i=>String(Math.floor(i/1000)).padStart(5,'0');
const sm=i=>`${D.small}/${pad(i)}/${i}.jpg`, big=i=>`${D.big}/${pad(i)}/${i}.jpg`;
let cleared=new Set();try{cleared=new Set(JSON.parse(localStorage.getItem('rescue-cleared')||'[]'))}catch(e){}
const save=()=>{try{localStorage.setItem('rescue-cleared',JSON.stringify([...cleared]))}catch(e){}};
const list=document.getElementById('list'), f=document.getElementById('f'), cnt=document.getElementById('cnt'), out=document.getElementById('out');
let shown=[], page=0; const PER=300;
function pick(){const v=f.value;return D.items.filter(x=>v==='all'||(v==='editor'&&x.e)||(v==='auto'&&!x.e)||(v==='low'&&!x.e&&x.c<0.99))}
function tile(x){return `<div class="ph${cleared.has(x.i)?' cleared':''}" data-i="${x.i}"><img loading="lazy" src="${sm(x.i)}" alt=""><span class="why">${x.w.join(' · ')}</span><label><input type="checkbox" ${cleared.has(x.i)?'checked':''}> not private</label></div>`}
function more(){const chunk=shown.slice(page*PER,(page+1)*PER);if(!chunk.length)return;page++;let html='',day=null,open=false;
 for(const x of chunk){if(x.d!==day){if(open)html+='</div>';html+=`<div class="day">${x.d||'undated'}<button data-day="${x.d}">mark whole day not private</button><button data-undo="${x.d}">unmark day</button></div><div class="grid">`;day=x.d;open=true}html+=tile(x)}
 if(open)html+='</div>';list.insertAdjacentHTML('beforeend',html)}
function render(){shown=pick();page=0;list.innerHTML='';more();upd()}
function upd(){cnt.textContent=`${shown.length.toLocaleString()} shown · ${cleared.size} marked not private`;if(out.style.display==='block')out.value=JSON.stringify([...cleared])}
addEventListener('scroll',()=>{if(innerHeight+scrollY>document.body.offsetHeight-1500)more()});
list.addEventListener('change',e=>{const ph=e.target.closest('.ph');const i=+ph.dataset.i;e.target.checked?cleared.add(i):cleared.delete(i);ph.classList.toggle('cleared',e.target.checked);save();upd()});
list.addEventListener('click',e=>{const db=e.target.closest('[data-day],[data-undo]');if(db){const d=db.dataset.day??db.dataset.undo;const on='day' in db.dataset;
  for(const x of shown)if(x.d===d){on?cleared.add(x.i):cleared.delete(x.i)}save();
  db.parentElement.nextElementSibling.querySelectorAll('.ph').forEach(ph=>{ph.classList.toggle('cleared',on);ph.querySelector('input').checked=on});upd();return}
 if(e.target.closest('label'))return;const ph=e.target.closest('.ph');if(!ph)return;const i=+ph.dataset.i;const x=D.items.find(y=>y.i===i);
 const lb=document.createElement('div');lb.className='lb';lb.innerHTML=`<img src="${big(i)}"><div>#${i} · ${x.d} · ${x.f} · ${x.w.join(' · ')}</div>`;lb.onclick=()=>lb.remove();document.body.appendChild(lb)});
document.getElementById('show').onclick=()=>{out.style.display='block';out.value=JSON.stringify([...cleared]);out.select()};
document.getElementById('reset').onclick=()=>{if(cleared.size&&!window.__confirmReset){window.__confirmReset=true;document.getElementById('reset').textContent='Click again to clear all marks';return}cleared.clear();save();window.__confirmReset=false;document.getElementById('reset').textContent='Reset marks';render()};
const API='http://127.0.0.1:8765/api', st=document.getElementById('st');
document.getElementById('apply').onclick=async()=>{if(!cleared.size){st.textContent='Nothing marked yet.';return}
 st.textContent=`Applying ${cleared.size}…`;
 try{const r=await fetch(API+'/private-cleared',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[...cleared]})});
  const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);
  st.textContent=`Applied (${j.cleared.toLocaleString()} cleared in total). Rebuilding pages…`;
  const poll=async()=>{try{const s=await (await fetch(API+'/status')).json();if(s.building){setTimeout(poll,2500);return}
   if(s.error){st.textContent='Rebuild failed: '+s.error;return}cleared.clear();save();location.reload()}catch(e){setTimeout(poll,4000)}};setTimeout(poll,2500)}
 catch(e){st.textContent='The local helper is not running. Ask Claude to start it, or press Show cleared IDs and paste the list.'}};
f.onchange=render;render();
</script></body></html>'''


if __name__ == '__main__':
    main()
