"""Streaming-estimator API with EXACT v0 stand-ins. See DESIGN_BRIEF §5.2.

Consumers code against this API; swap the internals for HyperLogLog / t-digest /
Count-Min when scale demands, without touching callers. Exact versions keep the
smoke path dependency-free and make tests deterministic.
"""
from __future__ import annotations

from collections import Counter

_QPOINTS = (0.10, 0.50, 0.90, 0.99)


class DistinctCount:
    """Distinct-count. v0: set-backed (swap: HyperLogLog)."""
    def __init__(self):
        self._s = set()

    def add(self, item):
        self._s.add(item)

    def __len__(self):
        return len(self._s)


class Quantiles:
    """Quantile sketch. v0: stores all values (swap: t-digest / DDSketch)."""
    def __init__(self):
        self._v: list[float] = []

    def add(self, value: float):
        self._v.append(float(value))

    def __len__(self) -> int:
        """Count of observations, not a bound on anything -- every intended v0
        replacement (t-digest, DDSketch) tracks this natively, so a caller that
        wants the sample size (not `._v`, which is a private list a swap can drop
        entirely) reads it through this instead of reaching past the API."""
        return len(self._v)

    def quantiles(self, points=_QPOINTS) -> list[float]:
        if not self._v:
            return [0.0] * len(points)
        v = sorted(self._v)
        n = len(v)
        return [v[min(int(p * n), n - 1)] for p in points]


class TopK:
    """Heavy hitters by weight. v0: full Counter (swap: Count-Min + heap)."""
    def __init__(self, k: int = 16):
        self.k = k
        self._c = Counter()

    def add(self, key: str, weight: float = 1.0):
        self._c[key] += weight

    def normalized(self) -> dict[str, float]:
        total = sum(self._c.values())
        if not total:
            return {}
        top = self._c.most_common(self.k)
        out = {k: round(w / total, 4) for k, w in top}
        rest = 1.0 - sum(out.values())
        if rest > 1e-9 and len(self._c) > self.k:
            out["other"] = round(rest, 4)
        return out
