"""Learn a personal taste score from the user's own picks, ratings and edits.

Positives: flagged pick, rating >= 3, or >= 3 develop-history steps.
Negatives: everything else from the same shoot days as a positive (what was passed over).
Reports held-out AUC for the generic LAION aesthetic score vs. the learned taste model.
"""
import json

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from config import WORK, work_db

EMB = WORK / 'embeddings'


def load_embeddings():
    parts = [(np.load(str(s).replace('.npy', '_ids.npy')), np.load(s).astype(np.float32))
             for s in sorted(EMB.glob('shard_*[0-9].npy'))]
    return np.concatenate([a for a, _ in parts]), np.concatenate([b for _, b in parts])


def main():
    db = work_db()
    ids, mat = load_embeddings()
    pos_of = {int(iid): i for i, iid in enumerate(ids)}
    rows = db.execute('''SELECT i.image_id, substr(i.capture_time,1,10), i.pick=1 OR i.rating>=3 OR i.history_steps>=3,
                                c.aesthetic
                         FROM images i JOIN clip c USING(image_id)''').fetchall()
    rows = [r for r in rows if r[0] in pos_of]
    pos_days = {d for _, d, p, _ in rows if p}
    train = [r for r in rows if r[1] in pos_days]
    X = mat[[pos_of[r[0]] for r in train]]
    y = np.array([int(r[2]) for r in train])
    groups = np.array([r[1] for r in train])
    aes = np.array([r[3] for r in train])
    print(f'training rows {len(train):,} across {len(pos_days):,} shoot days; positives {y.sum():,}')
    if y.sum() < 50 or len(pos_days) < 5:
        print('not enough signal to train')
        return

    oof = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=min(5, len(pos_days))).split(X, y, groups):
        m = LogisticRegression(C=0.5, max_iter=2000, class_weight='balanced').fit(X[tr], y[tr])
        oof[te] = m.decision_function(X[te])

    def within_day_auc(score):
        aucs = []
        for d in pos_days:
            mask = groups == d
            if 0 < y[mask].sum() < mask.sum():
                aucs.append(roc_auc_score(y[mask], score[mask]))
        return float(np.mean(aucs)), len(aucs)

    report = {
        'positives': int(y.sum()), 'rows': len(y), 'days': len(pos_days),
        'auc_aesthetic_overall': float(roc_auc_score(y, aes)),
        'auc_taste_overall_heldout': float(roc_auc_score(y, oof)),
        'auc_aesthetic_within_shoot': within_day_auc(aes),
        'auc_taste_within_shoot_heldout': within_day_auc(oof),
    }
    print(json.dumps(report, indent=2))

    model = LogisticRegression(C=0.5, max_iter=2000, class_weight='balanced').fit(X, y)
    taste = model.decision_function(mat)
    cols = [r[1] for r in db.execute('PRAGMA table_info(clip)')]
    if 'taste' not in cols:
        db.execute('ALTER TABLE clip ADD COLUMN taste REAL')
    db.executemany('UPDATE clip SET taste=? WHERE image_id=?', [(float(t), int(i)) for t, i in zip(taste, ids)])
    db.commit()
    np.save(WORK / 'taste_coef.npy', np.concatenate([model.coef_[0], model.intercept_]))
    (WORK / 'taste_report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
