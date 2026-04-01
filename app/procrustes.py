"""Generalized Procrustes Analysis and PCA via SVD.

NumPy only — no sklearn dependency.
"""
import numpy as np


def center(coords: np.ndarray) -> np.ndarray:
    """Translate landmarks to centroid origin."""
    return coords - coords.mean(axis=0)


def centroid_size(coords: np.ndarray) -> float:
    """Centroid size: square root of sum of squared distances from centroid."""
    centered = center(coords)
    return float(np.sqrt(np.sum(centered ** 2)))


def scale_to_unit(coords: np.ndarray) -> np.ndarray:
    """Scale landmarks to unit centroid size."""
    cs = centroid_size(coords)
    if cs < 1e-10:
        return coords
    return center(coords) / cs


def optimal_rotation(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Find optimal rotation matrix to align source to target (both centered and scaled)."""
    M = source.T @ target
    U, _, Vt = np.linalg.svd(M)
    # Handle reflection
    d = np.linalg.det(Vt.T @ U.T)
    D = np.diag([1, np.sign(d)])
    R = Vt.T @ D @ U.T
    return R


def procrustes_align_pair(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Align source to target via Procrustes (translate, scale, rotate)."""
    src = scale_to_unit(source)
    tgt = scale_to_unit(target)
    R = optimal_rotation(src, tgt)
    return src @ R.T


def generalized_procrustes(specimens: list[np.ndarray], max_iter: int = 100,
                           tol: float = 1e-6) -> tuple[list[np.ndarray], np.ndarray]:
    """Generalized Procrustes Analysis.

    Args:
        specimens: list of (N, 2) landmark arrays
        max_iter: maximum iterations
        tol: convergence tolerance

    Returns:
        aligned: list of aligned landmark arrays
        mean_shape: consensus mean shape
    """
    # Center and scale all specimens
    aligned = [scale_to_unit(s) for s in specimens]

    # Use first specimen as initial reference
    mean_shape = aligned[0].copy()

    for _ in range(max_iter):
        # Align all to current mean
        new_aligned = []
        for s in aligned:
            R = optimal_rotation(s, mean_shape)
            new_aligned.append(s @ R.T)

        # Compute new mean
        new_mean = np.mean(new_aligned, axis=0)
        new_mean = scale_to_unit(new_mean)

        # Check convergence
        diff = np.sum((new_mean - mean_shape) ** 2)
        aligned = new_aligned
        mean_shape = new_mean

        if diff < tol:
            break

    return aligned, mean_shape


def pca(aligned_specimens: list[np.ndarray], n_components: int = None) -> dict:
    """PCA on Procrustes-aligned specimens via SVD.

    Args:
        aligned_specimens: list of (N, 2) aligned landmark arrays
        n_components: number of PCs to retain (default: all)

    Returns:
        dict with keys: scores, loadings, explained_variance, explained_variance_ratio
    """
    # Flatten to (n_specimens, n_landmarks * 2)
    X = np.array([s.flatten() for s in aligned_specimens])
    X_centered = X - X.mean(axis=0)

    U, S, Vt = np.linalg.svd(X_centered, full_matrices=False)

    if n_components is None:
        n_components = min(X.shape)

    n_components = min(n_components, len(S))

    explained_var = (S ** 2) / (len(X) - 1)
    total_var = explained_var.sum()

    return {
        'scores': X_centered @ Vt[:n_components].T,
        'loadings': Vt[:n_components],
        'explained_variance': explained_var[:n_components],
        'explained_variance_ratio': explained_var[:n_components] / total_var if total_var > 0 else explained_var[:n_components],
        'singular_values': S[:n_components],
    }


def euclidean_distance_matrix(scores: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distance matrix from PCA scores."""
    n = len(scores)
    dist = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = np.linalg.norm(scores[i] - scores[j])
            dist[i, j] = d
            dist[j, i] = d
    return dist


def order_by_similarity(specimens: list[np.ndarray],
                        labels: list[str] = None) -> list[int]:
    """Order specimens by morphological similarity using PCA.

    Returns indices in order of similarity (nearest-neighbor chain).
    """
    aligned, _ = generalized_procrustes(specimens)
    result = pca(aligned)
    scores = result['scores']

    # Greedy nearest-neighbor starting from first specimen
    n = len(scores)
    visited = [False] * n
    order = [0]
    visited[0] = True

    for _ in range(n - 1):
        current = order[-1]
        best_dist = float('inf')
        best_idx = -1
        for j in range(n):
            if not visited[j]:
                d = np.linalg.norm(scores[current] - scores[j])
                if d < best_dist:
                    best_dist = d
                    best_idx = j
        order.append(best_idx)
        visited[best_idx] = True

    return order
