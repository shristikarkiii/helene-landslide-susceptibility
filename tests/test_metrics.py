import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from helene_metrics import auc, capture  # noqa: E402


def test_auc_perfect_and_inverted():
    assert auc([5, 6, 7], [1, 2, 3]) == 1.0
    assert auc([1, 2, 3], [5, 6, 7]) == 0.0


def test_auc_ties_count_half():
    assert auc([1, 1], [1, 1]) == 0.5
    assert auc([2, 1], [1, 0]) == 0.875


def test_capture_top_fraction():
    neg = list(range(100))                 # cutoff for top 10% is 90
    assert capture([95, 91, 50, 10], neg, 0.10) == 0.5
    assert capture([99], neg, 0.01) == 1.0


def test_bootstrap_interval_brackets_point_estimate():
    from helene_metrics import bootstrap_auc
    pos = [0.2, 0.6, 0.7, 0.8, 0.9] * 10
    neg = [0.1, 0.3, 0.4, 0.5, 0.65] * 40
    lo, hi = bootstrap_auc(pos, neg, n_boot=100)
    assert lo <= auc(pos, neg) <= hi
    assert 0 <= lo < hi <= 1
