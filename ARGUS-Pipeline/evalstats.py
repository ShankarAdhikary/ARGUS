"""Error-rate statistics shared by the offline biometric evaluation scripts (voice and fingerprint).

Conventions: a *genuine* trial compares two samples of the same source, an *impostor* trial two different sources; a higher
score means more similar. FAR (false-accept rate) is the share of impostor trials scoring at or above a threshold, FRR
(false-reject rate) the share of genuine trials scoring below it.
"""

from __future__ import annotations

import math

import numpy as np


def eer_of(genuine: np.ndarray, impostor: np.ndarray) -> tuple[float, float]:
    """(EER, threshold at the EER). Returns (nan, nan) if either side is empty. O(n log n): FAR/FRR from sorted scores."""
    if len(genuine) == 0 or len(impostor) == 0:
        return float("nan"), float("nan")
    grid = np.unique(np.concatenate([genuine, impostor]))
    imp, gen = np.sort(impostor), np.sort(genuine)
    far = 1.0 - np.searchsorted(imp, grid, side="left") / len(imp)        # share of impostors scoring >= t
    frr = np.searchsorted(gen, grid, side="left") / len(gen)              # share of genuine scoring < t
    k = int(np.argmin(np.abs(far - frr)))
    return float((far[k] + frr[k]) / 2), float(grid[k])


def threshold_at_far(impostor: np.ndarray, far: float) -> float | None:
    """Lowest threshold whose false-accept rate is at most `far`; None if there are too few impostor trials to support it.

    Supporting a rate p needs about 10/p impostor trials (10 expected errors): 1,000 for 1%, 10,000 for 0.1%, 100,000 for 0.01%.
    """
    if len(impostor) < 10.0 / far:
        return None
    return float(np.nextafter(np.quantile(impostor, 1.0 - far, method="higher"), np.inf))


def frr_at(genuine: np.ndarray, threshold: float | None) -> float | None:
    return None if threshold is None or len(genuine) == 0 else float((genuine < threshold).mean())


def rate_at(scores: np.ndarray, threshold: float, above: bool) -> float | None:
    """Share of `scores` at or above `threshold` (above=True: a FAR when scores are impostor) or below it (above=False: a FRR)."""
    if len(scores) == 0:
        return None
    return float((scores >= threshold).mean() if above else (scores < threshold).mean())


def upper_bound(errors: int, trials: int, z: float = 1.96) -> float | None:
    """95% upper confidence bound on an error rate seen as `errors` in `trials` (Wilson score; the rule of three for zero errors).

    "0 false accepts in 180 impostor trials" is NOT a false-accept rate of 0: it only shows the rate is below about 1.7%.
    """
    if trials <= 0:
        return None
    if errors == 0:
        return 3.0 / trials
    p = errors / trials
    denom = 1 + z * z / trials
    centre = p + z * z / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return min(1.0, (centre + margin) / denom)
