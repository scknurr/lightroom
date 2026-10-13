"""Write WORK/portfolio_final.json from the three portfolio stages, then rebuild pages and manifest."""
import json
import subprocess
import sys

from config import WORK

PF = WORK / 'portfolio'


def main():
    a = json.loads((PF / 'stage_a.json').read_text())['judged']
    b = json.loads((PF / 'stage_b.json').read_text())
    raw = json.load(open(sys.argv[1]))
    c = raw.get('result', raw)
    portfolio, theme_order, notes = [], {}, {}
    for th in b['themes']:
        theme_order[th['theme']] = th['portfolio']
        notes[th['theme']] = th.get('section_note', '')
        portfolio += [i for i in th['portfolio'] if i not in portfolio]
    book = [i for i in c['order'] if i in set(portfolio)]
    final = {'portfolio': portfolio, 'portfolio_set': portfolio, 'book': book, 'book_ranked': c['agreed'],
             'book_note': c.get('note', ''), 'theme_order': theme_order, 'section_notes': notes,
             'strength': {k: v['strength'] for k, v in a.items()}}
    (WORK / 'portfolio_final.json').write_text(json.dumps(final))
    print(f'final portfolio {len(portfolio):,} frames in {len(theme_order)} themes; book {len(book)}')
    for args in (['library_page.py'], ['ranked_page.py', str(WORK / 'rank' / 'finals.json')], ['library_manifest.py']):
        subprocess.run([sys.executable, *args], check=True)


if __name__ == '__main__':
    main()
