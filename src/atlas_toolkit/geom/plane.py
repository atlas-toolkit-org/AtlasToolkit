"""Plane frames and screen-stroke placement shared by mesh tools; no Qt or file I/O."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def reference_origin(bounds, reference):
    bounds = np.asarray(bounds, dtype=float)
    if reference == "world":
        return np.zeros(3)
    origin = bounds.mean(axis=0)
    if reference == "side":
        origin[0] = bounds[1, 0]
    elif reference != "center":
        raise ValueError(f"Unknown plane reference: {reference}")
    return origin


def plane(bounds, p):
    """World-space origin, unit normal and XYZ rotation; positions/offsets are in model units."""
    rotation = Rotation.from_euler("xyz", p["rotation"], degrees=True).as_matrix()
    normal = rotation[:, 2]
    origin = reference_origin(bounds, p["reference"]) + np.asarray(p["position"], dtype=float)
    return origin + normal * p["offset"], normal, rotation


def normal_angles(normal):
    n = np.asarray(normal, dtype=float).copy()
    length = np.linalg.norm(n)
    if not np.isfinite(length) or length < 1e-15:
        raise ValueError("A plane normal must be finite and nonzero.")
    n /= length
    return np.degrees([np.arctan2(-n[1], np.hypot(n[0], n[2])), np.arctan2(n[0], n[2]), 0.0])


def screen_plane(start, end, unproject, depth_point):
    """The infinite plane containing a screen stroke and its camera depth rays."""
    if np.linalg.norm(np.asarray(end) - start) < 8:
        raise ValueError("Draw a line at least 8 pixels long.")
    a, b = unproject(*start, -1), unproject(*end, -1)
    far = unproject(*start, 1)
    normal = np.cross(b - a, far - a)
    length = np.linalg.norm(normal)
    if not np.isfinite(length) or length < 1e-15:
        raise ValueError("The view cannot define this plane. Change the view and draw again.")
    normal /= length
    c = np.asarray(depth_point, dtype=float)
    return c - normal * np.dot(c - a, normal), normal


def reflect_points(points, origin, normal):
    """Reflect points without changing the caller's arrays."""
    points = np.asarray(points, dtype=float)
    normal = np.asarray(normal, dtype=float)
    normal = normal / np.linalg.norm(normal)
    return points - 2.0 * ((points - origin) @ normal)[..., None] * normal


def bounds_corners(bounds):
    lo, hi = np.asarray(bounds, dtype=float)
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
