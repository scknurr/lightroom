from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / '2026-10-05'
SNAPSHOT = AUDIT / 'catalog-audit-snapshot.sqlite'
PREVIEWS = Path('/Volumes/Y DRIVE/TOTAL/TOTAL-v13-3 Previews.lrdata')

WORK = Path('/Volumes/G DRIVE/LR-RESCUE.noindex')
DB = WORK / 'rescue.sqlite'
THUMBS = WORK / 'thumbs'
THUMB_LONG = 1440

# Preference order when a missing original has verified copies on several drives.
DRIVE_PREFERENCE = ['Y DRIVE', 'H DRIVE', 'L Drive', 'P DRIVE']


def ro(path):
    import sqlite3
    return sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)


def work_db():
    import sqlite3
    WORK.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=600)
    c.execute('PRAGMA journal_mode=WAL')
    return c


def thumb_path(image_id):
    return THUMBS / f'{image_id // 1000:05d}' / f'{image_id}.jpg'


PRIVATE_OVERRIDES = WORK / 'private_overrides.json'


def cleared_private():
    """Photo ids the owner marked "not private" on the private review page; they override every privacy flag."""
    import json
    try:
        return set(json.loads(PRIVATE_OVERRIDES.read_text())['cleared'])
    except FileNotFoundError:
        return set()


FINAL_PORTFOLIO = WORK / 'portfolio_final.json'


def final_portfolio():
    """The nailed-down portfolio ({portfolio, book, ...}) once the portfolio pass is applied, else None."""
    import json
    try:
        final = json.loads(FINAL_PORTFOLIO.read_text())
        final['portfolio_set'] = set(final['portfolio_set'])
        return final
    except FileNotFoundError:
        return None


def apply_final_verdict(iid, p, final):
    """Return the editor record with its verdict replaced by the final portfolio decision (copy)."""
    if not final or p.get('verdict') == 'skip':
        return p
    q = dict(p)
    q['verdict'] = 'portfolio' if iid in final['portfolio_set'] else 'backstock'
    return q
