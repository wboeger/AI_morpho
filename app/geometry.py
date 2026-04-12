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


def _split_contiguous(indices: list) -> list:
    """Split a list of indices into contiguous segments."""
    if not indices:
        return []
    segments = []
    current = [indices[0]]
    for i in range(1, len(indices)):
        if indices[i] == indices[i - 1] + 1:
            current.append(indices[i])
        else:
            segments.append(current)
            current = [indices[i]]
    segments.append(current)
    return segments


def _central_axis(landmarks: np.ndarray, indices: list,
                  ref_point: np.ndarray = None) -> np.ndarray:
    """Compute the central axis of a part from its landmark contour.

    Two cases:
    1. Part has TWO contiguous segments (e.g. Shaft [7..21, 72..92]):
       these are inner and outer edges. Pair corresponding points
       (resample to equal count) and compute midpoints → central axis.

    2. Part has ONE contiguous segment (e.g. SuperficialRoot [22..56]):
       the contour goes up one side, over the tip, and back down. Split
       at the tip (point farthest from ref_point/fork) into two halves,
       pair them, and compute midpoints → central axis.

    Returns array of midpoints ordered from base (fork end) to tip.
    """
    segments = _split_contiguous(indices)

    if len(segments) >= 2:
        # Two-segment part (inner/outer edges)
        seg1 = landmarks[segments[0]]
        seg2 = landmarks[segments[1]]

        # Orient both segments so they run in the same direction
        # (base → tip). If seg2 runs opposite, reverse it.
        if np.linalg.norm(seg1[0] - seg2[0]) > np.linalg.norm(seg1[0] - seg2[-1]):
            seg2 = seg2[::-1]

        # Resample to equal count and compute midpoints
        n = max(len(seg1), len(seg2))
        seg1_r = resample_equidistant(seg1, n)
        seg2_r = resample_equidistant(seg2, n)
        axis = (seg1_r + seg2_r) / 2.0
    else:
        # Single-segment part — split at tip
        coords = landmarks[indices]
        if ref_point is None:
            ref_point = (coords[0] + coords[-1]) / 2.0

        # Tip = point farthest from the reference (fork) point
        dists = np.linalg.norm(coords - ref_point, axis=1)
        tip_idx = int(np.argmax(dists))

        if tip_idx < 2 or tip_idx > len(coords) - 3:
            # Degenerate — return the coords as-is
            return coords

        side_a = coords[:tip_idx + 1]       # fork → tip
        side_b = coords[tip_idx:][::-1]     # tip → fork, reversed to fork → tip

        n = max(len(side_a), len(side_b))
        side_a_r = resample_equidistant(side_a, n)
        side_b_r = resample_equidistant(side_b, n)
        axis = (side_a_r + side_b_r) / 2.0

    return axis


def _midline_vector(coords: np.ndarray) -> np.ndarray:
    """Best-fit line direction through a set of 2D points (PCA first component).

    Falls back to endpoint-to-endpoint vector if fewer than 3 points.
    Always returns a unit vector.
    """
    if len(coords) < 2:
        return np.array([1.0, 0.0])
    if len(coords) < 3:
        v = coords[-1] - coords[0]
        n = np.linalg.norm(v)
        return v / n if n > 1e-10 else np.array([1.0, 0.0])

    centered = coords - coords.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    direction = vt[0]  # first principal component

    # Orient so it points from first to last point
    if np.dot(direction, coords[-1] - coords[0]) < 0:
        direction = -direction
    return direction


def fork_angle(landmarks: np.ndarray, boundary: dict,
               part_a_name: str, part_b_name: str, n_points: int = 5) -> float:
    """Angle at the fork where two parts diverge using central axes.

    Computes the true anatomical central axis for each part by pairing
    opposite contour sides:
      - Two-segment parts (e.g. Shaft [7..21, 72..92]): midpoints of
        inner and outer edges give the central axis.
      - Single-segment parts (e.g. SuperficialRoot [22..56]): the contour
        is split at the tip (farthest from fork) into two halves, then
        midpoints give the central axis.

    Both parts use only the PROXIMAL half (fork-end) of the central axis
    for fitting the midline direction. This captures the departure angle
    near the fork, ignoring distal curvature in both structures.
    """
    a_indices = boundary.get(part_a_name, [])
    b_indices = boundary.get(part_b_name, [])
    if not a_indices or not b_indices:
        return 0.0

    # Estimate the fork region as the midpoint between the closest
    # endpoints of the two parts
    a_ends = np.array([landmarks[a_indices[0]], landmarks[a_indices[-1]]])
    b_ends = np.array([landmarks[b_indices[0]], landmarks[b_indices[-1]]])
    min_dist = float('inf')
    fork_point = (a_ends[0] + b_ends[0]) / 2.0
    for ae in a_ends:
        for be in b_ends:
            d = np.linalg.norm(ae - be)
            if d < min_dist:
                min_dist = d
                fork_point = (ae + be) / 2.0

    # Compute central axes
    axis_a = _central_axis(landmarks, a_indices, ref_point=fork_point)
    axis_b = _central_axis(landmarks, b_indices, ref_point=fork_point)

    if len(axis_a) < 2 or len(axis_b) < 2:
        return 0.0

    # Orient axes so index 0 = fork end (closest to fork_point)
    if np.linalg.norm(axis_a[0] - fork_point) > np.linalg.norm(axis_a[-1] - fork_point):
        axis_a = axis_a[::-1]
    if np.linalg.norm(axis_b[0] - fork_point) > np.linalg.norm(axis_b[-1] - fork_point):
        axis_b = axis_b[::-1]

    # Both parts: midline through only the PROXIMAL half (fork-end),
    # capturing departure angle near the fork, ignoring distal curvature
    half_a = max(2, len(axis_a) // 2)
    half_b = max(2, len(axis_b) // 2)
    v1 = _midline_vector(axis_a[:half_a])
    v2 = _midline_vector(axis_b[:half_b])

    return angle_between_vectors(v1, v2)


def point_curvature_angle(landmarks: np.ndarray, boundary: dict) -> float:
    """Deviation angle between the point midline and the MIDDLE shaft midline.

    Unlike fork_angle, which uses the proximal (fork-end) half of each axis,
    this function uses:
      - Point: proximal half of the point axis (near the shaft junction).
      - Shaft: MIDDLE portion of the shaft axis (skipping the first and last
        quarter), avoiding the curve toward the point at the distal end.

    Returns the deviation from straight continuation (0° = aligned, 180° = opposite).
    """
    point_idx = boundary.get('Point', [])
    shaft_idx = boundary.get('Shaft', [])
    if not point_idx or not shaft_idx:
        return 0.0

    # Fork = Point–Shaft junction (closest pair of endpoints)
    a_ends = [landmarks[point_idx[0]], landmarks[point_idx[-1]]]
    b_ends = [landmarks[shaft_idx[0]], landmarks[shaft_idx[-1]]]
    min_dist = float('inf')
    fork_point = (a_ends[0] + b_ends[0]) / 2.0
    for ae in a_ends:
        for be in b_ends:
            d = np.linalg.norm(ae - be)
            if d < min_dist:
                min_dist = d
                fork_point = (ae + be) / 2.0

    # Point axis: direct line from mid-base (fork end) to tip.
    # axis_a[0] = midpoint of basal cross-section; axis_a[-1] = tip midpoint.
    axis_a = _central_axis(landmarks, point_idx, ref_point=fork_point)
    if len(axis_a) < 2:
        return 0.0
    if np.linalg.norm(axis_a[0] - fork_point) > np.linalg.norm(axis_a[-1] - fork_point):
        axis_a = axis_a[::-1]
    v1_raw = axis_a[-1] - axis_a[0]
    nrm1 = np.linalg.norm(v1_raw)
    if nrm1 < 1e-10:
        return 0.0
    v1 = v1_raw / nrm1

    # Shaft axis: middle third only (skip first and last third)
    axis_b = _central_axis(landmarks, shaft_idx, ref_point=fork_point)
    if len(axis_b) < 4:
        return 0.0
    if np.linalg.norm(axis_b[0] - fork_point) > np.linalg.norm(axis_b[-1] - fork_point):
        axis_b = axis_b[::-1]
    n = len(axis_b)
    start = max(1, n // 3)
    end = min(n - 1, 2 * n // 3)
    v2 = _midline_vector(axis_b[start:end])

    # PCA sign is arbitrary — orient v2 to point FROM junction TOWARD root.
    # axis_b[0] is at junction; centroid of middle third is toward root.
    mid_centroid = axis_b[start:end].mean(axis=0)
    if np.dot(v2, mid_centroid - fork_point) < 0:
        v2 = -v2

    # Acute Exterior Angle: the acute angle at the external junction between
    # the shaft midline and the point midline.
    # Full bend = angle between shaft-continuation (-v2) and point direction (v1)
    # = 180 - angle_between(v1, v2).
    # Take the acute version (always ≤ 90°): hooks bent past perpendicular
    # return the supplementary (acute) angle at the outer junction.
    bend = 180.0 - angle_between_vectors(v1, v2)
    return min(bend, 180.0 - bend)


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
