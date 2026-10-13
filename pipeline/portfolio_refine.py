"""Remove photos marked not-mine from the final portfolio, Book and Instagram queue; refill the Book from the
judges' remaining votes; rebuild pages and manifest. The Book order is marked for re-sequencing."""
import json
import subprocess
import sys

from config import FINAL_PORTFOLIO, WORK, not_mine

PF = WORK / 'portfolio'


def main():
    foreign = not_mine()
    final = json.loads(FINAL_PORTFOLIO.read_text())
    votes = json.loads((PF / 'book_votes.json').read_text())
    votes = votes.get('result', votes)['votes']
    size = len(final['book'])
    removed = [i for i in final['portfolio'] if i in foreign]
    final['portfolio'] = final['portfolio_set'] = [i for i in final['portfolio'] if i not in foreign]
    final['theme_order'] = {t: [i for i in ids if i not in foreign] for t, ids in final['theme_order'].items()}
    final['instagram_queue'] = [i for i in final.get('instagram_queue', []) if i not in foreign]
    port = set(final['portfolio'])
    book = [i for i in final['book'] if i in port]
    points = {}
    for v in votes:
        for r, i in enumerate(dict.fromkeys(v['picks'])):
            points[i] = points.get(i, 0) + (size - r)
    ranked = [i for i in sorted(points, key=lambda i: -points[i]) if i in port]
    added = []
    for i in ranked:
        if len(book) >= size:
            break
        if i not in book:
            book.append(i)
            added.append(i)
    final['book'] = book
    final['book_ranked'] = [i for i in ranked if i in set(book)]
    if added:
        final['book_needs_resequence'] = True
    FINAL_PORTFOLIO.write_text(json.dumps(final))
    print(f'removed {len(removed)} from portfolio ({len(final["portfolio"])} left); book refilled with {added}')
    for args in (['library_page.py'], ['ranked_page.py', str(WORK / 'rank' / 'finals.json')], ['library_manifest.py']):
        subprocess.run([sys.executable, *args], check=True)


if __name__ == '__main__':
    main()
