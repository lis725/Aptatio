"""Display-only reflected Gaussian KDE and inverse of the existing empirical map.

Scott bandwidth uses sample standard deviation (ddof=1) and ORIGINAL n**(-1/5).
Repeated sampled scores retain their multiplicity. Reflection adds boundary kernels,
not observations for bandwidth. Trapezoidal normalization is on model support [0,1].
Nothing here participates in assignment or percentile calculation.
"""
from functools import lru_cache
import math
import numpy as np

GRID_SIZE = 769
SUPPORT = (0.0, 1.0)


def density_curve(scores, reference_hash, *, grid_size=GRID_SIZE, bandwidth_scale=1.0):
    values = np.asarray(scores, dtype=float)
    if (values.ndim != 1 or values.size == 0 or not np.isfinite(values).all()
            or np.any(values < 0) or np.any(values > 1)):
        return {"status": "unavailable", "reason": "Invalid reference scores", "x": [], "density": []}
    if grid_size < 512 or grid_size > 1024 or not math.isfinite(bandwidth_scale) or bandwidth_scale <= 0:
        raise ValueError("Invalid density display settings")
    if values.size == 1 or np.ptp(values) == 0:
        return {"status": "point_mass", "point": float(values[0]), "sample_size": int(values.size),
                "support": list(SUPPORT), "x": [], "density": [], "bandwidth": None}
    # Including values as well as content hash protects callers even from an incorrect hash.
    x, y, bandwidth, error = _cached_curve(str(reference_hash), tuple(values), grid_size, float(bandwidth_scale))
    return {"status": "unavailable" if error else "ready", "reason": error,
            "x": list(x), "density": list(y), "bandwidth": bandwidth,
            "bandwidth_rule": "Scott: original sample std(ddof=1) * n**(-1/5)",
            "bandwidth_scale": bandwidth_scale, "boundary_method": "reflection_then_trapezoidal_normalization",
            "support": list(SUPPORT), "sample_size": int(values.size), "grid_size": grid_size}


@lru_cache(maxsize=8)
def _cached_curve(reference_hash, scores, grid_size, bandwidth_scale):
    original = np.asarray(scores, dtype=float)
    bandwidth = float(np.std(original, ddof=1) * original.size ** (-0.2) * bandwidth_scale)
    x = np.linspace(*SUPPORT, grid_size)
    if not math.isfinite(bandwidth) or bandwidth < (x[1] - x[0]) / 4:
        return (), (), bandwidth, "Reference density is too narrow for this plotting grid"
    y = np.zeros(grid_size)
    for offset in range(0, original.size, 512):
        block = original[offset:offset+512]
        for centers in (block, 2 * SUPPORT[0] - block, 2 * SUPPORT[1] - block):
            z = (x[:, None] - centers[None, :]) / bandwidth
            y += np.exp(-0.5 * z * z).sum(axis=1)
    y /= original.size * bandwidth * math.sqrt(2 * math.pi)
    area = float(np.trapezoid(y, x))
    if not math.isfinite(area) or area <= 0 or not np.isfinite(y).all():
        return (), (), bandwidth, "Reference density could not be estimated"
    y /= area
    return tuple(x), tuple(y), bandwidth, None


def threshold_marker(scores, percentile):
    """Invert average-tie-rank linear interpolation, never the KDE.

    At jumps into tail plateaus, or a point mass, no unique inverse exists;
    a type-7 quantile is explicitly a guide instead of an exact boundary.
    """
    values = np.asarray(scores, dtype=float)
    if (values.ndim != 1 or not values.size or not np.isfinite(values).all()
            or not math.isfinite(percentile) or not 0 <= percentile <= 1):
        return None
    unique, counts = np.unique(values, return_counts=True)
    ends = np.cumsum(counts) - 1
    starts = ends - counts + 1
    ranks = (starts + ends) / (2 * (values.size - 1)) if values.size > 1 else np.array([0.5])
    if unique.size > 1 and ranks[0] <= percentile <= ranks[-1] and 0 < percentile < 1:
        raw = float(np.interp(percentile, ranks, unique))
        return {"raw_score": raw, "percentile": percentile, "kind": "exact_empirical_inverse",
                "label": f"RN/PT threshold (percentile {percentile * 100:g})"}
    return {"raw_score": float(np.quantile(values, percentile)), "percentile": percentile,
            "kind": "quantile_guide", "label": f"RN/PT threshold quantile guide (percentile {percentile * 100:g})"}
