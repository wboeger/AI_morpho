"""Dataset loader for U-Net training."""
import os
import csv
import numpy as np
import torch
from torch.utils.data import Dataset
from PIL import Image
from unet.train import generate_heatmap


class LandmarkDataset(Dataset):
    """Dataset pairing structure images with landmark heatmaps.

    Expected directory structure:
        data_dir/
            images/          # PNG/JPG images
            landmarks/       # CSV files (same basename as images)
    """

    def __init__(self, data_dir: str, structure_type: str, img_size: int = 256,
                 augment: bool = True):
        self.img_size = img_size
        self.augment = augment
        self.samples = []

        img_dir = os.path.join(data_dir, 'images')
        lm_dir = os.path.join(data_dir, 'landmarks')

        if not os.path.isdir(img_dir) or not os.path.isdir(lm_dir):
            return

        for fname in os.listdir(img_dir):
            base = os.path.splitext(fname)[0]
            img_path = os.path.join(img_dir, fname)
            csv_path = os.path.join(lm_dir, base + '.csv')

            if os.path.exists(csv_path):
                self.samples.append((img_path, csv_path))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, csv_path = self.samples[idx]

        # Load image
        img = Image.open(img_path).convert('L')
        orig_w, orig_h = img.size
        img = img.resize((self.img_size, self.img_size))
        img_arr = np.array(img, dtype=np.float32) / 255.0

        # Load landmarks
        coords = []
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                try:
                    vals = [float(v) for v in row if v.strip()]
                    if len(vals) >= 2:
                        x = vals[-2] * self.img_size / orig_w
                        y = vals[-1] * self.img_size / orig_h
                        coords.append([x, y])
                except ValueError:
                    continue

        coords = np.array(coords)

        # Data augmentation
        if self.augment and np.random.rand() > 0.5:
            # Random rotation +-30 degrees
            angle = np.random.uniform(-30, 30)
            img_arr, coords = self._rotate(img_arr, coords, angle)

        if self.augment and np.random.rand() > 0.5:
            # Horizontal flip
            img_arr = np.fliplr(img_arr).copy()
            coords[:, 0] = self.img_size - coords[:, 0]

        if self.augment:
            # Brightness/contrast
            brightness = np.random.uniform(0.8, 1.2)
            contrast = np.random.uniform(0.8, 1.2)
            img_arr = np.clip((img_arr - 0.5) * contrast + 0.5 + (brightness - 1.0), 0, 1)

        # Generate heatmap
        heatmap = generate_heatmap(coords, self.img_size)

        # To tensors
        img_tensor = torch.from_numpy(img_arr).unsqueeze(0)  # (1, H, W)
        hm_tensor = torch.from_numpy(heatmap).unsqueeze(0)   # (1, H, W)

        return img_tensor, hm_tensor

    def _rotate(self, img, coords, angle_deg):
        """Rotate image and coordinates."""
        from PIL import Image as PILImage
        pil_img = PILImage.fromarray((img * 255).astype(np.uint8))
        pil_img = pil_img.rotate(-angle_deg, resample=PILImage.BILINEAR,
                                  center=(self.img_size/2, self.img_size/2))
        img_rot = np.array(pil_img, dtype=np.float32) / 255.0

        # Rotate coordinates
        angle_rad = np.radians(angle_deg)
        cx, cy = self.img_size / 2, self.img_size / 2
        cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
        coords_rot = coords.copy()
        dx = coords[:, 0] - cx
        dy = coords[:, 1] - cy
        coords_rot[:, 0] = cos_a * dx - sin_a * dy + cx
        coords_rot[:, 1] = sin_a * dx + cos_a * dy + cy

        return img_rot, coords_rot
