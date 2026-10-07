"""Contact sheets for the editor pass: 3x3 grids of 560px tiles, each labeled with a slot letter + image id."""
import argparse
import json

from PIL import Image, ImageDraw, ImageFont

from config import WORK, thumb_path, work_db

TILE = 560
COLS = ROWS = 3
SLOTS = 'ABCDEFGHI'
SHEETS = WORK / 'sheets'

STYLE_PROFILE = {
    'summary': 'When this photographer edits, they favor punchy midtone clarity (+15 to +27), a negative post-crop '
               'vignette (about -25), a touch of dehaze (0 to +13), a slightly warm, slightly magenta white balance, '
               'Adobe Standard profile, and rarely black and white (~2.5% of edits) or grain. Tone sliders are used '
               'sparingly and the look stays natural rather than heavily stylized.',
    'medians_when_used': {'Clarity2012': 17, 'PostCropVignetteAmount': -25, 'Dehaze': 3, 'Texture': -5, 'Tint': 6,
                          'Saturation': 5, 'GrainAmount': 7},
}


def font(size):
    for p in ('/System/Library/Fonts/SFNSMono.ttf', '/System/Library/Fonts/Menlo.ttc', '/Library/Fonts/Arial.ttf'):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build(ids, name):
    sheet = Image.new('RGB', (COLS * TILE, ROWS * TILE), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    f = font(22)
    slots = {}
    for k, iid in enumerate(ids[:COLS * ROWS]):
        im = Image.open(thumb_path(iid)).convert('RGB')
        im.thumbnail((TILE - 8, TILE - 40))
        x0, y0 = (k % COLS) * TILE, (k // COLS) * TILE
        sheet.paste(im, (x0 + (TILE - im.width) // 2, y0 + 34 + (TILE - 40 - im.height) // 2))
        label = f'{SLOTS[k]}  #{iid}  {im.width}x{im.height}'
        draw.rectangle([x0, y0, x0 + TILE, y0 + 30], fill=(0, 0, 0))
        draw.text((x0 + 8, y0 + 4), label, fill=(255, 210, 90), font=f)
        slots[SLOTS[k]] = iid
    path = SHEETS / f'{name}.jpg'
    sheet.save(path, quality=86)
    return {'sheet': str(path), 'slots': slots}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', default='2019')
    ap.add_argument('--tiers', default='hero,select')
    ap.add_argument('--suffix', default='', help='name suffix for an incremental batch, e.g. "b"')
    ap.add_argument('--days', help='comma-separated capture days (YYYY-MM-DD); overrides --year filter')
    ap.add_argument('--name', help='sheet set name (default: the year)')
    args = ap.parse_args()
    SHEETS.mkdir(parents=True, exist_ok=True)
    db = work_db()
    tiers = args.tiers.split(',')
    if args.days:
        days = args.days.split(',')
        rows = db.execute(f'''SELECT s.image_id FROM selection s JOIN images i USING(image_id)
                              WHERE substr(i.capture_time,1,10) IN ({",".join("?" * len(days))})
                              AND s.tier IN ({",".join("?" * len(tiers))}) ORDER BY i.capture_time''',
                          [*days, *tiers]).fetchall()
    else:
        rows = db.execute(f'''SELECT s.image_id FROM selection s JOIN images i USING(image_id)
                              WHERE i.year=? AND s.tier IN ({",".join("?" * len(tiers))})
                              ORDER BY i.capture_time''', [args.year, *tiers]).fetchall()
    done = set()
    for f in WORK.glob(f'editor_{args.name or args.year}*.json'):
        done |= {p['image_id'] for p in json.loads(f.read_text())['photos']}
    ids = [r[0] for r in rows if r[0] not in done]
    tag = f'{args.name or args.year}{args.suffix}'
    sheets = [build(ids[i:i + 9], f'{tag}_{i // 9:04d}') for i in range(0, len(ids), 9)]
    out = WORK / f'sheets_{tag}.json'
    out.write_text(json.dumps({'style_profile': STYLE_PROFILE, 'sheets': sheets}, indent=1))
    print(f'{len(ids):,} photos -> {len(sheets)} sheets; index {out}')


if __name__ == '__main__':
    main()
