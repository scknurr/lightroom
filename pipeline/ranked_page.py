"""Rank every reviewed keeper 1..N and render WORK/ranked.html.

  1-60      three judges (impact / craft / story) over the finalists, Borda-combined
  61-1504   the judged top tier (editor 9s and portfolio 8s), ordered by position within its ranked group
  1505-N    everything else, by editor score, then portfolio before backstock, then model score
"""
import argparse
import json

from config import WORK, final_portfolio
from rank_prepare import keepers

TOP = 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('finals', help='rank-finals workflow output file')
    args = ap.parse_args()
    keep, model = keepers()
    orders = {int(k): v for k, v in json.loads((WORK / 'rank' / 'group_orders.json').read_text()).items()}
    raw = json.load(open(args.finals))
    judges = raw.get('result', raw)['judges']

    pos = {}
    for o in orders.values():
        for r, i in enumerate(o):
            pos[i] = r / max(len(o) - 1, 1)
    points = {}
    for j in judges:
        for r, i in enumerate(dict.fromkeys(j['top60'])):
            if i in keep:
                points[i] = points.get(i, 0) + (TOP - r)
    top = sorted(points, key=lambda i: (-points[i], pos.get(i, 1), -(model.get(i) or 0)))[:TOP]
    tier2 = sorted((i for i in pos if i in keep and i not in set(top)), key=lambda i: (pos[i], -keep[i]['editor_score'], -(model.get(i) or 0)))
    used = set(top) | set(tier2)
    rest = sorted((i for i in keep if i not in used),
                  key=lambda i: (-keep[i]['editor_score'], keep[i]['verdict'] != 'portfolio', -(model.get(i) or 0)))
    final = final_portfolio()
    if final:
        # Nailed-down portfolio: the Book (by combined judge votes), then the rest of the final portfolio
        # by strict-pass strength, then everything else in its previous order.
        book = [i for i in final['book_ranked'] if i in keep and i in final['portfolio_set']]
        bset = set(book)
        st = {int(k): v for k, v in final['strength'].items()}
        prev = top + tier2 + rest
        pidx = {i: n for n, i in enumerate(prev)}
        port = sorted((i for i in final['portfolio_set'] if i in keep and i not in bset), key=lambda i: (-st.get(i, 0), pidx.get(i, 1e9)))
        used = bset | set(port)
        ranking = [(i, 'book') for i in book] + [(i, 'portfolio') for i in port] + [(i, 'score') for i in prev if i not in used]
    else:
        ranking = [(i, 'judges') for i in top] + [(i, 'group') for i in tier2] + [(i, 'score') for i in rest]
    assert len(ranking) == len(keep) == len({i for i, _ in ranking})
    (WORK / 'ranking.json').write_text(json.dumps([{'rank': n + 1, 'image_id': i, 'basis': b} for n, (i, b) in enumerate(ranking)]))

    from library_page import SMALL, make_small
    from concurrent.futures import ThreadPoolExecutor
    from config import thumb_path, work_db
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(make_small, keep))
    db = work_db()
    meta = {iid: (y or '', d or '') for iid, y, d in db.execute('SELECT image_id, year, substr(capture_time,1,10) FROM images')}
    photos = []
    for i, b in ranking:
        p = keep[i]
        y, d = meta.get(i, ('', ''))
        photos.append({'i': i, 'b': b[0], 'v': 'P' if p['verdict'] == 'portfolio' else 'B', 's': p.get('editor_score', 0),
                       't': p.get('title', ''), 'c': p.get('caption', ''), 'k': p.get('keywords', [])[:12], 'y': y, 'd': d,
                       'u': p.get('uses', []), 'r': p.get('release_notes', []), 'm': bool(p.get('minors')),
                       'n': (p.get('treatment') or {}).get('notes', ''), 'w': p.get('why', '')})
    labels = {'judges': ['Judges’ top 60', 'three independent judges (impact, craft, story), combined'],
              'group': ['Judged top tier', 'ranked by eye in mixed groups of 36, by placement in group'],
              'book': ['The Book', 'the flagship set: three judges picked from every theme’s best, votes combined'],
              'portfolio': ['Portfolio', 'the rest of the nailed-down portfolio, by strict-pass strength'],
              'score': ['Everything else', 'by editor score, then portfolio before backstock, then the model score']}
    sections = []
    for n, (_, b) in enumerate(ranking):
        if not sections or sections[-1][1] != b:
            sections.append([n, b])
    data = {'sections': [[s, labels[b][0], labels[b][1], 'top' if b in ('judges', 'book') else 'g'] for s, b in sections],
            'photos': photos, 'judges': [{'lens': j['lens'], 'why': j['why_first']} for j in judges],
            'small': 'file://' + str(SMALL).replace(' ', '%20'), 'big': 'file://' + str(thumb_path(0).parent.parent).replace(' ', '%20')}
    out = WORK / 'ranked.html'
    out.write_text(PAGE.replace('__DATA__', json.dumps(data, separators=(',', ':')).replace('</', '<\\/')))
    from collections import Counter
    print(f'ranked {len(ranking):,}:', ', '.join(f'{n:,} {b}' for b, n in Counter(b for _, b in ranking).items()), f'-> {out}')


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ranked Library</title>
<style>
:root{--bg:#0d0d0f;--panel:#151619;--line:#24262b;--ink:#ecebe7;--mut:#8e9099;--acc:#e8b44b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(13,13,15,.95);border-bottom:1px solid var(--line)}
.bar{max-width:1700px;margin:0 auto;padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0}a{color:var(--acc)}input{background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:6px 10px;border-radius:99px;font:inherit;width:120px}
.mut{color:var(--mut);font-size:13px}
main{max-width:1700px;margin:0 auto;padding:14px 16px 80px}
.legend{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 16px;margin-bottom:14px;color:#d6d4cf}
.legend li{margin:4px 0}
.sec{margin:22px 0 8px;font-size:15px;color:var(--acc)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px}
.grid.top{grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.ph{position:relative;background:#000;border-radius:6px;overflow:hidden;cursor:zoom-in;aspect-ratio:3/2}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph .n{position:absolute;left:6px;top:6px;font-size:12px;font-weight:600;padding:1px 8px;border-radius:99px;background:rgba(0,0,0,.72);color:var(--acc)}
.ph .cap{position:absolute;inset:auto 0 0 0;padding:16px 7px 5px;font-size:11px;background:linear-gradient(transparent,rgba(0,0,0,.8));opacity:0;transition:.15s}
.ph:hover .cap{opacity:1}
.lb{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.93);display:grid;grid-template-columns:1fr minmax(260px,360px)}
.lb img{max-width:100%;max-height:100vh;object-fit:contain;margin:auto;display:block;align-self:center}
.lb aside{background:var(--panel);padding:20px;overflow:auto;border-left:1px solid var(--line)}
.lb .kw span{display:inline-block;background:#202227;border-radius:99px;padding:2px 9px;margin:3px 4px 0 0;font-size:12px}
.lb .row{color:var(--mut);font-size:12px;margin-top:10px}.lb .row b{color:var(--ink);font-weight:500}
.lb .x{position:absolute;right:376px;top:12px;font-size:26px;color:#fff;cursor:pointer;background:none;border:0}
@media (max-width:760px){.lb{grid-template-columns:1fr}.lb aside{max-height:40vh}.lb .x{right:12px}}
</style></head><body>
<header><div class="bar"><h1>Ranked Library</h1><span class="mut" id="tot"></span><a href="library.html">Library</a><a href="private.html">Private review</a>
<label class="mut">Jump to # <input id="jump" type="number" min="1"></label></div></header>
<main><ul class="legend">
<li id="why"></li></ul><div id="list"></div></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent), N=D.photos.length;
const pad=i=>String(Math.floor(i/1000)).padStart(5,'0');
const sm=i=>`${D.small}/${pad(i)}/${i}.jpg`, big=i=>`${D.big}/${pad(i)}/${i}.jpg`;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('tot').textContent=`${N.toLocaleString()} photos`;
document.getElementById('why').innerHTML='Why #1: '+D.judges.map(j=>`<i>${esc(j.lens)}</i>: ${esc(j.why)}`).join(' · ');
const list=document.getElementById('list'); let shown=0; const PER=240;
const SECS=D.sections.map(s=>[s[0],s[1],s[3]]);
document.querySelector('.legend').insertAdjacentHTML('afterbegin',D.sections.map((s,k)=>`<li><b>#${(s[0]+1).toLocaleString()}–${((D.sections[k+1]||[N])[0]).toLocaleString()} ${s[1]}</b>: ${s[2]}.</li>`).join(''));
function tile(k){const p=D.photos[k];return `<div class="ph" data-k="${k}"><img loading="lazy" src="${sm(p.i)}" alt=""><span class="n">#${k+1}</span><div class="cap">${esc(p.t)}</div></div>`}
let cur=null, curSec=-1;
function more(){if(shown>=N)return;const end=Math.min(N,shown+PER);let buf=[];
 const flush=()=>{if(buf.length){cur.insertAdjacentHTML('beforeend',buf.join(''));buf=[]}};
 for(let k=shown;k<end;k++){let si=0;for(let j=0;j<SECS.length;j++)if(SECS[j][0]<=k)si=j;
  if(si!==curSec){flush();list.insertAdjacentHTML('beforeend',`<div class="sec">${SECS[si][1]}</div><div class="grid ${SECS[si][2]==='top'?'top':''}"></div>`);cur=list.lastElementChild;curSec=si}
  buf.push(tile(k))}
 flush();shown=end}
addEventListener('scroll',()=>{if(innerHeight+scrollY>document.body.offsetHeight-2000)more()});
document.getElementById('jump').onchange=e=>{const t=Math.max(1,Math.min(N,+e.target.value||1))-1;while(shown<=t)more();
 const el=list.querySelector(`[data-k="${t}"]`);if(el){el.scrollIntoView({block:'center'});el.style.outline='3px solid var(--acc)'}};
list.onclick=e=>{const ph=e.target.closest('.ph');if(!ph)return;const k=+ph.dataset.k,p=D.photos[k];
 const lb=document.createElement('div');lb.className='lb';lb.innerHTML=`<img src="${big(p.i)}" alt=""><aside><h3>#${k+1} · ${esc(p.t)}</h3><div>${esc(p.c)}</div>
 <div class="kw">${p.k.map(x=>`<span>${esc(x)}</span>`).join('')}</div>
 <div class="row"><b>${p.v==='P'?'Portfolio':'Backstock'}</b> · editor ${p.s}/10 · ${esc(p.d||p.y)} · #${p.i} · ranked by ${({j:'judges',g:'group',b:'the Book panel',p:'portfolio strength',s:'score'})[p.b]}</div>
 ${p.w?`<div class="row">${esc(p.w)}</div>`:''}${p.n?`<div class="row"><b>Edit:</b> ${esc(p.n)}</div>`:''}
 <div class="row"><b>Uses:</b> ${esc(p.u.join(', ')||'—')}${p.r.length?` · ${esc(p.r.join(', ').replaceAll('_',' '))}`:''}${p.m?' · <b>minors</b>':''}</div></aside><button class="x" aria-label="Close">×</button>`;
 lb.onclick=ev=>{if(ev.target===lb||ev.target.classList.contains('x'))lb.remove()};document.body.appendChild(lb);
 const key=ev=>{if(ev.key==='Escape'){lb.remove();removeEventListener('keydown',key)}};addEventListener('keydown',key)};
more();more();
</script></body></html>'''


if __name__ == '__main__':
    main()
