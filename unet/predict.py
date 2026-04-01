"""Inference: predict landmarks from a structure image using trained U-Net."""
import os
import numpy as np
import torch
from PIL import Image
from unet.model import create_model


def predict_landmarks(image_path: str, structure_type: str, weights_dir: str,
                      n_landmarks: int = 100, img_size: int = 256) -> list:
    """Predict landmark coordinates from a structure image.

    Args:
        image_path: path to the structure image
        structure_type: 'hook', 'anchor', etc.
        weights_dir: directory containing model weights
        n_landmarks: number of landmarks to extract
        img_size: model input size

    Returns:
        list of [x, y] coordinates in original image space, or empty list if no model.
    """
    weights_path = os.path.join(weights_dir, f'{structure_type}_best.pth')
    if not os.path.exists(weights_path):
        return []

    device = torch.device('cuda' if torch.cuda.is_available() else
                          'mps' if torch.backends.mps.is_available() else 'cpu')

    model = create_model(structure_type).to(device)
    model.load_state_dict(torch.load(weights_path, map_location=device, weights_only=True))
    model.eval()

    # Load and preprocess image
    img = Image.open(image_path).convert('L')
    orig_w, orig_h = img.size
    img_resized = img.resize((img_size, img_size))
    img_arr = np.array(img_resized, dtype=np.float32) / 255.0
    img_tensor = torch.from_numpy(img_arr).unsqueeze(0).unsqueeze(0).to(device)

    # Predict heatmap
    with torch.no_grad():
        heatmap = model(img_tensor).squeeze().cpu().numpy()

    # Extract landmarks from heatmap
    # Threshold and find peaks
    heatmap = np.clip(heatmap, 0, None)
    if heatmap.max() > 0:
        heatmap /= heatmap.max()

    # Find contour points above threshold
    threshold = 0.3
    points = np.argwhere(heatmap > threshold)  # (y, x) format

    if len(points) < 3:
        return []

    # Order points along the outline using nearest-neighbor
    ordered = _order_points(points)

    # Resample to desired count
    from app.geometry import resample_equidistant
    ordered_xy = np.array([[p[1], p[0]] for p in ordered], dtype=np.float64)
    resampled = resample_equidistant(ordered_xy, n_landmarks)

    # Scale back to original image coordinates
    scale_x = orig_w / img_size
    scale_y = orig_h / img_size
    landmarks = [[float(p[0] * scale_x), float(p[1] * scale_y)] for p in resampled]

    return landmarks


def _order_points(points: np.ndarray) -> list:
    """Order 2D points along a path using nearest-neighbor traversal."""
    remaining = list(range(len(points)))
    ordered = [remaining.pop(0)]

    while remaining:
        last = points[ordered[-1]]
        dists = [np.sum((points[r] - last) ** 2) for r in remaining]
        nearest_idx = np.argmin(dists)
        ordered.append(remaining.pop(nearest_idx))

    return [points[i] for i in ordered]
