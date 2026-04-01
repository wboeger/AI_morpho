"""Training script for U-Net landmark detection models.

Progressive training: retrain as new confirmed landmarks accumulate.
"""
import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from unet.model import create_model
from unet.dataset import LandmarkDataset


def generate_heatmap(landmarks: np.ndarray, img_size: int = 256, sigma: float = 3.0) -> np.ndarray:
    """Generate a combined heatmap with Gaussian blobs at each landmark position."""
    heatmap = np.zeros((img_size, img_size), dtype=np.float32)
    for (x, y) in landmarks:
        # Scale landmarks to image size
        xi, yi = int(x), int(y)
        if 0 <= xi < img_size and 0 <= yi < img_size:
            y_grid, x_grid = np.ogrid[max(0, yi-10):min(img_size, yi+10),
                                       max(0, xi-10):min(img_size, xi+10)]
            gauss = np.exp(-((x_grid - xi)**2 + (y_grid - yi)**2) / (2 * sigma**2))
            heatmap[max(0, yi-10):min(img_size, yi+10),
                    max(0, xi-10):min(img_size, xi+10)] = np.maximum(
                heatmap[max(0, yi-10):min(img_size, yi+10),
                        max(0, xi-10):min(img_size, xi+10)], gauss)
    return heatmap


def train_model(structure_type: str, data_dir: str, weights_dir: str,
                epochs: int = 50, batch_size: int = 8, lr: float = 1e-4):
    """Train a U-Net model for a specific structure type.

    Args:
        structure_type: 'hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco'
        data_dir: directory containing training images and landmark CSVs
        weights_dir: directory to save model weights
        epochs: number of training epochs
        batch_size: batch size
        lr: learning rate
    """
    device = torch.device('cuda' if torch.cuda.is_available() else
                          'mps' if torch.backends.mps.is_available() else 'cpu')

    model = create_model(structure_type).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()

    dataset = LandmarkDataset(data_dir, structure_type)
    if len(dataset) == 0:
        print(f"No training data for {structure_type}")
        return

    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    print(f"Training {structure_type} model on {len(dataset)} samples, device={device}")

    best_loss = float('inf')
    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for images, heatmaps in dataloader:
            images = images.to(device)
            heatmaps = heatmaps.to(device)

            pred = model(images)
            loss = criterion(pred, heatmaps)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item()

        avg_loss = total_loss / len(dataloader)
        if (epoch + 1) % 10 == 0:
            print(f"  Epoch {epoch+1}/{epochs}, Loss: {avg_loss:.6f}")

        if avg_loss < best_loss:
            best_loss = avg_loss
            os.makedirs(weights_dir, exist_ok=True)
            path = os.path.join(weights_dir, f'{structure_type}_best.pth')
            torch.save(model.state_dict(), path)

    print(f"Training complete. Best loss: {best_loss:.6f}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--structure', required=True, choices=['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco'])
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--weights-dir', default='weights')
    parser.add_argument('--epochs', type=int, default=50)
    args = parser.parse_args()

    train_model(args.structure, args.data_dir, args.weights_dir, args.epochs)
