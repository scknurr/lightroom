"""Build a self-contained HTML dashboard from the audit snapshot + CSV reports.

Read-only. Produces 2026-10-05/dashboard.html — open in a browser, no server needed.
"""
import csv
import html
import json
import sqlite3
from pathlib import Path


OUT = Path(__file__).resolve().parent / '2026-10-05'


def load_summary():
    return json.loads((OUT / 'summary.json').read_text())


def load_relink():
    return json.loads((OUT / 'relink-summary.json').read_text())


def load_drive_scan():
    p = OUT / 'drive-scan-summary.json'
    return json.loads(p.read_text()) if p.exists() else None


def load_drive_hits():
    import os
    p = OUT / 'drive-hits-missing.csv'
    if not p.exists():
        return {}
    rows = list(csv.DictReader(p.open()))
    # Verified-size hits only, best drive per file_id
    best = {}
    for r in rows:
        if r['size_match'] != 'True':
            continue
        fid = r['file_id']
        if fid not in best:
            best[fid] = r
    # Group as missing_parent -> drive -> count
    mapping = {}
    for r in best.values():
        mp = os.path.dirname(r['missing_path'])
        hp = os.path.dirname(r['hit_path'])
        key = (mp, r['drive'], hp)
        mapping[key] = mapping.get(key, 0) + 1
    return {'best_by_fid': best, 'folder_map': mapping}


def bucket_rows():
    verified = list(csv.DictReader((OUT / 'relink-candidates-verified.csv').open()))
    missing = list(csv.DictReader((OUT / 'missing-original-paths.csv').open()))
    # Representative example per bucket
    priority = {'verified_same_photo': 4, 'weak_match': 3, 'needs_file_read': 2,
                'conflict': 1, 'different_photo': 0, 'unverifiable': -1}
    best = {}
    for row in verified:
        mid = row['missing_file_id']
        if mid not in best or priority[row['verdict']] > priority[best[mid]['verdict']]:
            best[mid] = row
    examples = {}
    for mr in missing:
        v = best.get(mr['file_id'])
        if v and v['verdict'] == 'verified_same_photo' and mr['path'].startswith('/Volumes/Y DRIVE/Pictures/2019'):
            examples.setdefault('nested-2019', (mr['path'], v['candidate_path']))
        if v and v['verdict'] == 'verified_same_photo' and mr['path'].startswith('/Users/Steve/Dropbox/'):
            examples.setdefault('dropbox-2015', (mr['path'], v['candidate_path']))
        if mr['path'].startswith('/Users/Steve/Pictures/2020/'):
            examples.setdefault('local-2020', (mr['path'], None))
    return examples


def top_folders():
    missing = list(csv.DictReader((OUT / 'missing-original-paths.csv').open()))
    from collections import Counter
    import os
    return Counter(os.path.dirname(r['path']) for r in missing).most_common(10)


def main():
    s = load_summary()
    r = load_relink()
    ds = load_drive_scan()
    dh = load_drive_hits()
    examples = bucket_rows()
    top_missing = top_folders()

    tagged_pct = 100 * s['tagged_image_records'] / s['image_records']
    found = s['file_states'].get('found', 0)
    file_absent = s['file_states'].get('file_absent', 0)
    folder_absent = s['file_states'].get('folder_absent', 0)
    missing_total = file_absent + folder_absent
    found_pct = 100 * found / s['file_records']

    years = s['capture_years']
    year_counts = {y['year']: y['count'] for y in years}
    max_year_count = max(year_counts.values())

    formats = s['formats']
    total_fmt = sum(f['count'] for f in formats)

    roots = s['roots']

    # Buckets in a stable order for display.
    bucket_order = ['local-2020', 'nested-2019', 'downloads', 'camera-card', 'dropbox-2015', 'ydrive-other', 'desktop']
    bucket_labels = {
        'local-2020': '/Users/Steve/Pictures/2020/ (gone from this Mac)',
        'nested-2019': '/Volumes/Y DRIVE/Pictures/2019/2019-07-19/… (wrongly-nested import)',
        'downloads': '/Users/steve/Downloads/… (temporary files)',
        'camera-card': '/Volumes/EOS_DIGITAL 1/… (camera card)',
        'dropbox-2015': '/Users/Steve/Dropbox/2015/… (old Dropbox)',
        'ydrive-other': 'Other paths on Y DRIVE',
        'desktop': 'Desktop',
    }
    bucket_meaning = {
        'verified_same_photo': ('catalog duplicate', 'Same photo already in catalog at a different (reachable) path. The missing entry is a stale duplicate — no originals lost.'),
        'weak_match': ('possible match', 'File size matches an existing cataloged photo, but capture time differs. Likely a filename collision but worth a closer look.'),
        'needs_file_read': ('possibly recoverable', 'A file with that name sits on disk but is not in the catalog. Lightroom alone can\'t tell if it\'s the same photo — would need to read EXIF.'),
        'different_photo': ('genuinely missing', 'The only files found with that name are definitely different photos (different capture time AND size). The original is not reachable from any path we checked.'),
        'no_filename_candidate': ('genuinely missing', 'Nothing with that filename was found anywhere we looked.'),
        'conflict': ('conflict', 'Capture time matches but file size differs — likely re-edited/re-exported variant.'),
        'unverifiable': ('unknown', 'Could not evaluate.'),
    }

    def row_html(bk):
        counts = r['bucket_breakdown'].get(bk, {})
        total = sum(counts.values())
        if total == 0:
            return ''
        parts = []
        for v, (label, _tip) in bucket_meaning.items():
            n = counts.get(v, 0)
            if n:
                parts.append(f'<span class="pill pill-{v}" title="{html.escape(bucket_meaning[v][1])}">{n:,} {label}</span>')
        ex = examples.get(bk)
        example_html = ''
        if ex:
            if ex[1]:
                example_html = f'<div class="example"><div class="ex-label">Example</div><div class="ex-row"><span class="ex-missing">{html.escape(ex[0])}</span></div><div class="ex-row">↳ already here: <span class="ex-found">{html.escape(ex[1])}</span></div></div>'
            else:
                example_html = f'<div class="example"><div class="ex-label">Example missing path</div><div class="ex-row"><span class="ex-missing">{html.escape(ex[0])}</span></div></div>'
        return f'''<div class="bucket">
          <div class="bucket-head"><strong>{total:,}</strong> files · <span class="bucket-label">{html.escape(bucket_labels[bk])}</span></div>
          <div class="pills">{''.join(parts)}</div>
          {example_html}
        </div>'''

    buckets_html = '\n'.join(row_html(bk) for bk in bucket_order)

    year_bars_html = '\n'.join(
        f'<div class="ybar"><div class="ylabel">{html.escape(y["year"])}</div>'
        f'<div class="ybarwrap"><div class="ybarfill" style="width:{100*y["count"]/max_year_count:.1f}%"></div></div>'
        f'<div class="ycount">{y["count"]:,}</div></div>'
        for y in years
    )

    format_bars_html = '\n'.join(
        f'<div class="fbar"><div class="flabel">{html.escape(f["fileFormat"])}</div>'
        f'<div class="fbarwrap"><div class="fbarfill" style="width:{100*f["count"]/total_fmt:.1f}%"></div></div>'
        f'<div class="fcount">{f["count"]:,} <span class="fpct">({100*f["count"]/total_fmt:.1f}%)</span></div></div>'
        for f in formats
    )

    roots_rows = '\n'.join(
        f'<tr><td class="path">{html.escape(root)}</td>'
        f'<td class="num">{counts.get("found",0):,}</td>'
        f'<td class="num bad">{counts.get("file_absent",0):,}</td>'
        f'<td class="num bad">{counts.get("folder_absent",0):,}</td></tr>'
        for root, counts in sorted(roots.items(), key=lambda kv: -(kv[1].get('found', 0)))
    )

    top_missing_rows = '\n'.join(
        f'<tr><td class="num">{n:,}</td><td class="path">{html.escape(p)}</td></tr>'
        for p, n in top_missing
    )

    verified_total = r['per_missing_file_best_verdict'].get('verified_same_photo', 0)
    needs_read = r['per_missing_file_best_verdict'].get('needs_file_read', 0)
    # Drive scan supersedes the earlier catalog-only "truly missing" count
    reclaim_on_drives = ds['missing_with_size_verified_hit'] if ds else 0
    reclaim_name_only = (ds['missing_with_any_drive_hit'] - reclaim_on_drives) if ds else 0
    truly_unaccounted = s['file_states'].get('file_absent', 0) + s['file_states'].get('folder_absent', 0) - (ds['missing_with_any_drive_hit'] if ds else 0)

    hero_text = f'''
      <div class="hero">
        <div class="hero-row"><div class="big">{s['image_records']:,}</div><div class="hero-label">photos in your catalog</div></div>
        <div class="hero-row warn"><div class="big">{s['tagged_image_records']:,}</div><div class="hero-label">have any keyword <span class="small">({tagged_pct:.2f}%)</span></div></div>
        <div class="hero-row ok"><div class="big">{found:,}</div><div class="hero-label">originals Lightroom can still find <span class="small">({found_pct:.2f}%)</span></div></div>
        <div class="hero-row bad"><div class="big">{missing_total:,}</div><div class="hero-label">originals marked missing in Lightroom</div></div>
      </div>
    '''

    recap_text = f'''
      <ul class="recap">
        <li><span class="tag tag-dupe">{reclaim_on_drives:,}</span> are <b>recoverable right now</b> — I scanned every attached drive and found exact copies (same filename AND file size) on H DRIVE and L Drive. Lightroom just needs to be pointed at them.</li>
        <li><span class="tag tag-maybe">{reclaim_name_only:,}</span> have a <b>same-name file on a drive but with a different size</b>. Possibly re-exported variants; worth a closer look.</li>
        <li><span class="tag tag-miss">{max(0,truly_unaccounted):,}</span> appear <b>not on any attached drive</b>. Candidates for Time Machine or a drive we haven't connected yet.</li>
      </ul>
    '''

    # Build drive-scan section
    drive_scan_html = ''
    if ds:
        drive_rows = ''
        for drv, v in ds['per_drive_scan'].items():
            sv = ds['size_verified_hits_by_drive'].get(drv, 0)
            no = ds['name_only_hits_by_drive'].get(drv, 0)
            drive_rows += f'<tr><td class="path">{html.escape(drv)}</td><td class="num">{v["media_files"]:,}</td><td class="num ok">{sv:,}</td><td class="num">{no:,}</td><td class="num small">{v["elapsed_seconds"]:.0f}s</td></tr>'
        # Folder mapping (biggest moves first)
        folder_rows = ''
        from collections import defaultdict
        best_by_mp = defaultdict(lambda: {'count':0,'drive':'','hp':''})
        for (mp, drv, hp), n in dh.get('folder_map', {}).items():
            if n > best_by_mp[mp]['count']:
                best_by_mp[mp] = {'count': n, 'drive': drv, 'hp': hp}
        for mp, v in sorted(best_by_mp.items(), key=lambda kv: -kv[1]['count']):
            folder_rows += f'''<tr>
              <td class="num">{v['count']:,}</td>
              <td class="path"><span class="ex-missing">{html.escape(mp)}</span></td>
              <td class="path"><span class="ex-found">{html.escape(v['hp'])}</span> <span class="small">({html.escape(v['drive'])})</span></td>
            </tr>'''
        drive_scan_html = f'''
  <div class="section">
    <h2 style="margin-top:0">All attached drives — the real picture</h2>
    <p class="sub" style="margin:-4px 0 12px">I walked every drive looking for the 2,242 "missing" filenames and counting media overall. Lightroom only knows about Y DRIVE + a few local Mac folders.</p>
    <table>
      <thead><tr><th>Drive</th><th class="num">Media files</th><th class="num">Verified matches to missing</th><th class="num">Name-only hits</th><th class="num">Scan time</th></tr></thead>
      <tbody>{drive_rows}</tbody>
    </table>
    <p class="sub" style="margin-top:14px">The catalog indexes <b>{s['file_records']:,}</b> files. All seven attached drives together hold <b>{sum(v["media_files"] for v in ds["per_drive_scan"].values()):,}</b> media files — the catalog knows about roughly <b>{100*s['file_records']/sum(v["media_files"] for v in ds["per_drive_scan"].values()):.1f}%</b> of what's on your drives.</p>
  </div>

  <div class="section">
    <h2 style="margin-top:0">Where the "missing" photos actually live</h2>
    <p class="sub" style="margin:-4px 0 12px">Each row is a missing folder in the catalog mapped to the exact drive folder where I found the matching files (same filename + same file size).</p>
    <table>
      <thead><tr><th class="num">Files</th><th>Catalog thinks they're here (missing)</th><th>Actually here (verified)</th></tr></thead>
      <tbody>{folder_rows}</tbody>
    </table>
  </div>
        '''

    page = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Lightroom Rescue — Audit Dashboard</title>
<style>
  :root {{
    --bg: #0e0f12; --card: #15171c; --ink: #e9eaee; --muted: #9aa0ac;
    --ok: #4ade80; --warn: #f59e0b; --bad: #ef4444; --dupe: #60a5fa;
    --maybe: #a78bfa; --accent: #22d3ee;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
    background: var(--bg); color: var(--ink); line-height: 1.5;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 32px 24px 80px; }}
  h1 {{ font-size: 28px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin: 0 0 24px; }}
  h2 {{ font-size: 18px; margin: 28px 0 10px; letter-spacing: .3px; }}
  .section {{ background: var(--card); border-radius: 12px; padding: 20px 22px; margin: 14px 0; }}
  .hero {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }}
  .hero-row {{ padding: 14px 16px; background: #1a1d24; border-radius: 10px; display: flex; align-items: baseline; gap: 14px; border-left: 3px solid var(--muted); }}
  .hero-row.ok {{ border-left-color: var(--ok); }}
  .hero-row.warn {{ border-left-color: var(--warn); }}
  .hero-row.bad {{ border-left-color: var(--bad); }}
  .big {{ font-size: 30px; font-weight: 600; letter-spacing: -0.5px; }}
  .hero-label {{ color: var(--muted); font-size: 14px; }}
  .small {{ color: var(--muted); font-size: 12px; }}
  code {{ background: #1a1d24; padding: 1px 6px; border-radius: 4px; font-size: 13px; }}
  .recap {{ list-style: none; padding: 0; margin: 0; }}
  .recap li {{ padding: 10px 0; border-bottom: 1px solid #22252d; }}
  .recap li:last-child {{ border: none; }}
  .tag {{ display: inline-block; min-width: 70px; text-align: center; padding: 2px 10px; border-radius: 999px; font-weight: 600; margin-right: 6px; font-size: 13px; }}
  .tag-dupe {{ background: rgba(96,165,250,.15); color: var(--dupe); }}
  .tag-maybe {{ background: rgba(167,139,250,.15); color: var(--maybe); }}
  .tag-miss {{ background: rgba(239,68,68,.15); color: var(--bad); }}
  .bucket {{ padding: 12px 14px; background: #1a1d24; border-radius: 10px; margin: 8px 0; }}
  .bucket-head {{ margin-bottom: 8px; }}
  .bucket-label {{ color: var(--muted); font-size: 14px; }}
  .pills {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .pill {{ padding: 2px 10px; border-radius: 999px; font-size: 12px; cursor: help; }}
  .pill-verified_same_photo {{ background: rgba(96,165,250,.18); color: var(--dupe); }}
  .pill-weak_match {{ background: rgba(167,139,250,.18); color: var(--maybe); }}
  .pill-needs_file_read {{ background: rgba(167,139,250,.18); color: var(--maybe); }}
  .pill-different_photo {{ background: rgba(239,68,68,.18); color: var(--bad); }}
  .pill-no_filename_candidate {{ background: rgba(239,68,68,.18); color: var(--bad); }}
  .pill-conflict {{ background: rgba(245,158,11,.18); color: var(--warn); }}
  .example {{ margin-top: 8px; padding: 8px 10px; background: #11131a; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .ex-label {{ color: var(--muted); font-size: 11px; margin-bottom: 2px; letter-spacing: .5px; text-transform: uppercase; }}
  .ex-row {{ color: var(--muted); }}
  .ex-missing {{ color: var(--bad); }}
  .ex-found {{ color: var(--ok); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid #22252d; }}
  th {{ color: var(--muted); font-weight: 500; font-size: 12px; letter-spacing: .4px; text-transform: uppercase; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .num.bad {{ color: var(--bad); }}
  .path {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .ybar, .fbar {{ display: grid; grid-template-columns: 56px 1fr 110px; align-items: center; gap: 8px; padding: 2px 0; }}
  .ylabel, .flabel {{ color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
  .ybarwrap, .fbarwrap {{ background: #1a1d24; height: 8px; border-radius: 999px; overflow: hidden; }}
  .ybarfill {{ background: linear-gradient(90deg, var(--accent), var(--dupe)); height: 100%; }}
  .fbarfill {{ background: var(--accent); height: 100%; }}
  .ycount, .fcount {{ text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; }}
  .fpct {{ color: var(--muted); }}
  .limits {{ color: var(--muted); font-size: 12px; }}
  .limits li {{ margin: 4px 0; }}
  .nextsteps li {{ margin: 6px 0; }}
  .footer {{ color: var(--muted); font-size: 12px; margin-top: 32px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Lightroom Rescue — Audit Dashboard</h1>
  <p class="sub">Catalog: <code>{html.escape(s['catalog'])}</code> &nbsp;·&nbsp; Snapshot: 2026-10-05 &nbsp;·&nbsp; Read-only — nothing in Lightroom was changed.</p>

  <div class="section">
    <h2 style="margin-top:0">Big picture</h2>
    {hero_text}
  </div>

  <div class="section">
    <h2 style="margin-top:0">What "missing" actually means</h2>
    <p class="sub" style="margin: -4px 0 12px">Lightroom shows {missing_total:,} missing originals. After scanning all 7 attached drives for exact filename + size matches:</p>
    {recap_text}
  </div>
  {drive_scan_html}

  <div class="section">
    <h2 style="margin-top:0">Where the missing files were last seen</h2>
    <p class="sub" style="margin:-4px 0 12px">Grouped by original folder, with a verdict for each. Hover a pill for what it means.</p>
    {buckets_html}
  </div>

  <div class="section">
    <h2 style="margin-top:0">Where your photos live now</h2>
    <table>
      <thead><tr><th>Root path</th><th class="num">Found</th><th class="num">File absent</th><th class="num">Folder absent</th></tr></thead>
      <tbody>{roots_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <h2 style="margin-top:0">Top folders missing from disk</h2>
    <table>
      <thead><tr><th class="num">Files</th><th>Folder</th></tr></thead>
      <tbody>{top_missing_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <h2 style="margin-top:0">Keyword coverage</h2>
    <p class="sub" style="margin:-4px 0 12px">This is the biggest gap. <b>{s['tagged_image_records']:,}</b> of <b>{s['image_records']:,}</b> photos have any keyword at all ({tagged_pct:.2f}%). <b>{s['keyword_records']:,}</b> keywords exist in total.</p>
  </div>

  <div class="section">
    <h2 style="margin-top:0">Capture years</h2>
    {year_bars_html}
  </div>

  <div class="section">
    <h2 style="margin-top:0">File formats</h2>
    {format_bars_html}
  </div>

  <div class="section">
    <h2 style="margin-top:0">Companion files &amp; extras in catalog folders</h2>
    <p>• <b>{s['catalog_media_companions']:,}</b> JPEG/other companion files sit next to RAWs that already know about them (via sidecar extensions). These are expected and not an issue.</p>
    <p>• <b>{s['extra_media_in_catalog_folders']:,}</b> media files sit inside catalog folders but aren't in the catalog. These might be worth importing.</p>
    <p>• <b>{s['duplicate_candidate_groups_unverified']:,}</b> groups of photos share filename + capture time inside the catalog (possible internal duplicates — not yet verified by content).</p>
  </div>

  <div class="section">
    <h2 style="margin-top:0">What next, in Lightroom</h2>
    <ol class="nextsteps">
      <li><b>Relink <code>~/Pictures/2020</code> to H DRIVE.</b> In Lightroom's Folders panel, right-click <code>2020</code> under <code>Pictures</code> and choose "Update Folder Location…" → point at <code>/Volumes/H DRIVE/Pictures/2020</code>. That one action reconnects ~1,654 photos.</li>
      <li><b>Delete the stale "nested-2019" catalog entries.</b> The 376 files under <code>/Volumes/Y DRIVE/Pictures/2019/2019-07-19/2019/2019-09-25</code> are a broken double-nested import; the real originals already exist as proper catalog entries at <code>/Volumes/Y DRIVE/2019/2019-09-25</code>. Safe to remove the stale entries only.</li>
      <li><b>Decide what L Drive and P DRIVE are for.</b> Both mirror Y DRIVE's year-folder structure almost exactly. If they're backups, Lightroom doesn't need to see them. If they contain photos Y doesn't have, they should get imported.</li>
      <li><b>Fix the keyword gap.</b> 99.94% of the catalog is untagged. Even bulk auto-keywording (people/places/events) is a bigger win than any file reorganization.</li>
      <li><b>Decide whether to import the uncataloged media.</b> There are hundreds of thousands of photo/video files on your drives that Lightroom doesn't know about. Some are camera-card dumps, some are video project leftovers, some are real photos.</li>
    </ol>
  </div>

  <div class="section">
    <h2 style="margin-top:0">Limits of this audit</h2>
    <ul class="limits">
      {''.join(f'<li>{html.escape(x)}</li>' for x in s['limits'])}
    </ul>
  </div>

  <p class="footer">Generated by <code>build_dashboard.py</code> from the read-only catalog snapshot. Open any CSV in <code>2026-10-05/</code> in Numbers/Excel for row-level detail.</p>
</div>
</body>
</html>
'''
    (OUT / 'dashboard.html').write_text(page)
    print('Wrote', OUT / 'dashboard.html')


if __name__ == '__main__':
    main()
