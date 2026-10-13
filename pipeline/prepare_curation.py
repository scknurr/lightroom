"""Write compact per-year keeper lists and a portfolio list for the curation workflow; print its args."""
import json
import sqlite3

from config import WORK, work_db


def main():
    db = work_db()
    meta = {iid: (y or 'undated', d) for iid, y, d in db.execute('SELECT image_id, year, substr(capture_time,1,10) FROM images')}
    recs = {}
    for f in sorted(WORK.glob('editor_*.json')):
        for p in json.loads(f.read_text())['photos']:
            recs[p['image_id']] = p
    out = WORK / 'curate'
    out.mkdir(exist_ok=True)
    by_year, portfolio = {}, []
    for iid, p in recs.items():
        if p['verdict'] == 'skip' or p.get('private'):
            continue
        y, d = meta.get(iid, ('undated', ''))
        y = y if y >= '2008' else '2000-2007 and undated'
        r = {'id': iid, 'v': p['verdict'], 's': p['editor_score'], 'theme': p['theme'], 'title': p['title'],
             'kw': p['keywords'][:6], 'people': p['people'], 'minors': p['minors'], 'uses': p['uses'], 'day': d}
        by_year.setdefault(y, []).append(r)
        if p['verdict'] == 'portfolio':
            portfolio.append({k: r[k] for k in ('id', 's', 'title', 'theme', 'people', 'minors', 'uses')} | {'year': y})
    years = []
    for y, rows in sorted(by_year.items()):
        rows.sort(key=lambda r: r['day'])
        f = out / f'{y}.json'
        f.write_text(json.dumps(rows, separators=(',', ':')))
        years.append({'name': y, 'file': str(f)})
    pf = out / 'portfolio.json'
    pf.write_text(json.dumps(portfolio, separators=(',', ':')))
    args = {'years': years, 'portfolioFile': str(pf)}
    (out / 'args.json').write_text(json.dumps(args))
    print(sum(len(v) for v in by_year.values()), 'keepers;', len(portfolio), 'portfolio;', len(years), 'years')
    print(json.dumps(args))


if __name__ == '__main__':
    main()
