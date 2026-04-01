"""Geometric computation functions for landmark-based character analysis.

All functions operate on NumPy arrays of shape (N, 2) representing
ordered 2D landmark coordinates.
"""
import numpy as np


def arc_length(coords: np.ndarray) -> float:
    """Sum of Euclidean distances between consecutive points."""
    if len(coords) < 2:
        return 0.0
    diffs = np.diff(coords, axis=0)
    return float(np.sum(np.sqrt(np.sum(diffs ** 2, axis=1))))


def chord_length(coords: np.ndarray) -> float:
    """Straight-line distance from first to last point."""
    if len(coords) < 2:
        return 0.0
    return float(np.linalg.norm(coords[-1] - coords[0]))


def sinuosity(coords: np.ndarray) -> float:
    """Arc length / chord length. Returns 1.0 for perfectly straight outlines."""
    c = chord_length(coords)
    if c < 1e-10:
        return 1.0
    return arc_length(coords) / c


def local_curvature(coords: np.ndarray) -> np.ndarray:
    """Menger curvature at each interior point using the circumscribed circle
    of three consecutive points.

    Returns array of length N-2 (one value per interior point).
    Positive = curving left, negative = curving right.
    """
    if len(coords) < 3:
        return np.array([])

    curvatures = np.zeros(len(coords) - 2)
    for i in range(len(coords) - 2):
        p1, p2, p3 = coords[i], coords[i + 1], coords[i + 2]
        # Signed area of triangle
        area = 0.5 * ((p2[0] - p1[0]) * (p3[1] - p1[1]) -
                       (p3[0] - p1[0]) * (p2[1] - p1[1]))
        d12 = np.linalg.norm(p2 - p1)
        d23 = np.linalg.norm(p3 - p2)
        d13 = np.linalg.norm(p3 - p1)
        denom = d12 * d23 * d13
        if denom < 1e-10:
            curvatures[i] = 0.0
        else:
            curvatures[i] = 4.0 * area / denom

    return curvatures


def mean_curvature(coords: np.ndarray) -> float:
    """Mean of absolute local curvature values."""
    k = local_curvature(coords)
    if len(k) == 0:
        return 0.0
    return float(np.mean(np.abs(k)))


def max_curvature(coords: np.ndarray) -> float:
    """Maximum absolute local curvature."""
    k = local_curvature(coords)
    if len(k) == 0:
        return 0.0
    return float(np.max(np.abs(k)))


def curvature_variance(coords: np.ndarray) -> float:
    """Variance of absolute local curvature — used for adaptive landmark count."""
    k = local_curvature(coords)
    if len(k) == 0:
        return 0.0
    return float(np.var(np.abs(k)))


def direction_vector(coords: np.ndarray, n_points: int = 5, end: str = 'start') -> np.ndarray:
    """Average direction vector over the first or last n_points.

    Args:
        coords: landmark coordinates
        n_points: number of points to average over
        end: 'start' for beginning of part, 'end' for end of part
    """
    n = min(n_points, len(coords) - 1)
    if n < 1:
        return np.array([1.0, 0.0])

    if end == 'start':
        segment = coords[:n + 1]
    else:
        segment = coords[-(n + 1):]

    diffs = np.diff(segment, axis=0)
    avg = np.mean(diffs, axis=0)
    norm = np.linalg.norm(avg)
    if norm < 1e-10:
        return np.array([1.0, 0.0])
    return avg / norm


def angle_between_vectors(v1: np.ndarray, v2: np.ndarray) -> float:
    """Angle in degrees between two 2D direction vectors (0-180)."""
    cos_angle = np.clip(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-10), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos_angle)))


def junction_angle(part_a_coords: np.ndarray, part_b_coords: np.ndarray,
                   n_points: int = 5) -> float:
    """Angle at the junction between two consecutive parts.

    Uses direction vectors at the end of part_a and start of part_b.
    """
    v1 = direction_vector(part_a_coords, n_points, end='end')
    v2 = direction_vector(part_b_coords, n_points, end='start')
    return angle_between_vectors(v1, v2)


def relative_vertical_position(coords_a: np.ndarray, coords_b: np.ndarray) -> float:
    """Normalized vertical displacement between tips of two parts.

    Positive = A tip is below B tip (larger Y).
    Normalized by total outline extent.
    """
    tip_a = coords_a[0]  # first point = tip
    tip_b = coords_b[0]
    all_coords = np.vstack([coords_a, coords_b])
    extent = np.ptp(all_coords[:, 1])
    if extent < 1e-10:
        return 0.0
    return float((tip_a[1] - tip_b[1]) / extent)


def circularity(coords: np.ndarray) -> float:
    """4 * pi * area / perimeter^2. Requires a closed or near-closed contour.

    Uses the shoelace formula for area.
    """
    n = len(coords)
    if n < 3:
        return 0.0

    # Shoelace formula for signed area
    x, y = coords[:, 0], coords[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    perim = arc_length(coords)

    if perim < 1e-10:
        return 0.0
    return float(4.0 * np.pi * area / (perim ** 2))


def resample_equidistant(coords: np.ndarray, n_points: int) -> np.ndarray:
    """Resample a contour to n_points equidistant landmarks."""
    if len(coords) < 2:
        return coords

    # Compute cumulative arc length
    diffs = np.diff(coords, axis=0)
    segment_lengths = np.sqrt(np.sum(diffs ** 2, axis=1))
    cumulative = np.zeros(len(coords))
    cumulative[1:] = np.cumsum(segment_lengths)
    total = cumulative[-1]

    if total < 1e-10:
        return np.tile(coords[0], (n_points, 1))

    # Interpolate at equidistant positions
    target_positions = np.linspace(0, total, n_points)
    new_coords = np.zeros((n_points, 2))

    for i, t in enumerate(target_positions):
        idx = np.searchsorted(cumulative, t, side='right') - 1
        idx = np.clip(idx, 0, len(coords) - 2)
        seg_len = cumulative[idx + 1] - cumulative[idx]
        if seg_len < 1e-10:
            frac = 0.0
        else:
            frac = (t - cumulative[idx]) / seg_len
        new_coords[i] = coords[idx] + frac * (coords[idx + 1] - coords[idx])

    return new_coords


def suggest_landmark_count(coords_50: np.ndarray, structure_type: str) -> int:
    """Suggest adaptive landmark count based on curvature complexity.

    Args:
        coords_50: initial 50-point outline
        structure_type: one of 'superficial_bar', 'deep_bar', 'mco'
    """
    from config import Config

    ranges = Config.ADAPTIVE_RANGES
    if structure_type not in ranges:
        return 100  # fixed for hook/anchor

    min_count, max_count = ranges[structure_type]
    cv = curvature_variance(coords_50)

    # Map curvature variance to landmark count
    # Low complexity (cv < 0.001) → min_count
    # High complexity (cv > 0.01) → max_count
    cv_min, cv_max = 0.001, 0.01
    t = np.clip((cv - cv_min) / (cv_max - cv_min), 0, 1)
    count = int(min_count + t * (max_count - min_count))

    # Round to nearest 10
    return max(min_count, min(max_count, round(count / 10) * 10))
