"""Tests for geometric computation functions."""
import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.geometry import (
    arc_length, chord_length, sinuosity, local_curvature,
    mean_curvature, direction_vector, junction_angle,
    resample_equidistant, suggest_landmark_count,
)


def test_arc_length_straight_line():
    coords = np.array([[0, 0], [1, 0], [2, 0], [3, 0]])
    assert abs(arc_length(coords) - 3.0) < 1e-10


def test_chord_length():
    coords = np.array([[0, 0], [1, 1], [3, 0]])
    assert abs(chord_length(coords) - 3.0) < 1e-10


def test_sinuosity_straight():
    coords = np.array([[0, 0], [1, 0], [2, 0]])
    assert abs(sinuosity(coords) - 1.0) < 1e-10


def test_sinuosity_curved():
    # Semicircle should have sinuosity > 1
    t = np.linspace(0, np.pi, 50)
    coords = np.column_stack([np.cos(t), np.sin(t)])
    s = sinuosity(coords)
    assert s > 1.0


def test_local_curvature_straight():
    coords = np.array([[0, 0], [1, 0], [2, 0], [3, 0]])
    k = local_curvature(coords)
    assert len(k) == 2
    assert all(abs(ki) < 1e-10 for ki in k)


def test_local_curvature_circle():
    # Points on a unit circle should have curvature ~1
    t = np.linspace(0, np.pi/2, 20)
    coords = np.column_stack([np.cos(t), np.sin(t)])
    k = local_curvature(coords)
    # Menger curvature for unit circle should be close to 1
    assert np.mean(np.abs(k)) > 0.5


def test_direction_vector():
    coords = np.array([[0, 0], [1, 0], [2, 0]])
    v = direction_vector(coords, n_points=2, end='start')
    assert abs(v[0] - 1.0) < 1e-10
    assert abs(v[1]) < 1e-10


def test_junction_angle_right_angle():
    part_a = np.array([[0, 0], [1, 0], [2, 0]])  # horizontal
    part_b = np.array([[2, 0], [2, 1], [2, 2]])   # vertical
    angle = junction_angle(part_a, part_b, n_points=2)
    assert abs(angle - 90.0) < 5.0  # approximately 90


def test_resample_equidistant():
    coords = np.array([[0, 0], [10, 0]])
    resampled = resample_equidistant(coords, 11)
    assert len(resampled) == 11
    assert abs(resampled[5][0] - 5.0) < 1e-10


def test_resample_preserves_endpoints():
    coords = np.array([[0, 0], [5, 5], [10, 0]])
    resampled = resample_equidistant(coords, 50)
    assert abs(resampled[0][0]) < 1e-6
    assert abs(resampled[-1][0] - 10.0) < 1e-6


if __name__ == '__main__':
    test_arc_length_straight_line()
    test_chord_length()
    test_sinuosity_straight()
    test_sinuosity_curved()
    test_local_curvature_straight()
    test_local_curvature_circle()
    test_direction_vector()
    test_junction_angle_right_angle()
    test_resample_equidistant()
    test_resample_preserves_endpoints()
    print("All geometry tests passed!")
