"""Scoring helpers for the toolbox. Pure Python so they can be unit-tested
without an ArcGIS licence."""


def auc(pos, neg):
    """Mann-Whitney AUC: P(score at a landslide > score at a random cell),
    ties counted as half."""
    ranked = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        avg = (i + j + 1) / 2.0            # 1-based average rank for ties
        rank_sum += avg * sum(1 for k in range(i, j) if ranked[k][1])
        i = j
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def capture(pos, neg, top_fraction):
    """Share of landslides whose score is at or above the cutoff that marks
    the highest-scoring `top_fraction` of background cells."""
    cut = sorted(neg, reverse=True)[max(0, int(len(neg) * top_fraction) - 1)]
    return sum(1 for v in pos if v >= cut) / len(pos)


def bootstrap_auc(pos, neg, n_boot=200, seed=599):
    """95% percentile interval for the AUC, resampling landslides and
    background cells with replacement."""
    import random
    rng = random.Random(seed)
    draws = sorted(
        auc([rng.choice(pos) for _ in pos], [rng.choice(neg) for _ in neg])
        for _ in range(n_boot))
    return draws[int(0.025 * n_boot)], draws[int(0.975 * n_boot) - 1]
