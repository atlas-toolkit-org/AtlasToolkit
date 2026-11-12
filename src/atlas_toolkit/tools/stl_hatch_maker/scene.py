"""
The Hatch Maker's scene: a mesh seen by an orthographic camera, laid out on paper in millimetres.

Camera: azimuth / elevation in degrees, the 3D viewer's (pyqtgraph) convention, so a view picked on screen is the
view drawn: the camera sits at centre + d·(cos el·cos az, cos el·sin az, sin el), Z up; roll turns the picture.
Camera space: x right, y up, z towards the viewer. Paper space: mm, y down, fitted inside the margins.

Visibility: rays cast towards the model (embree when installed, else trimesh's own) answer, for any paper point,
which face is seen there and how deep. Every line style, outline and crease is cut by the same test, so hidden
lines are removed the same way everywhere.
"""
from __future__ import annotations

import math

import numpy as np

from atlas_toolkit.geom.paper import fit, sheet
from atlas_toolkit.geom.polylines import trails

LIGHTS = {   # camera space; z towards the viewer
    "front": (0.0, 0.0, 1.0),          # v5: a headlight
    "top-left": (-0.4, 0.6, 0.7),      # v2.1
    "top-right": (0.4, 0.6, 0.7),
    "top": (0.0, 0.8, 0.6),
    "left": (-0.8, 0.2, 0.6),
    "right": (0.8, 0.2, 0.6),
}


def camera_basis(azimuth: float, elevation: float, roll: float = 0.0) -> np.ndarray:
    """Rows: right, up, towards-viewer (world -> camera rotation)."""
    az, el, rl = (math.radians(a) for a in (azimuth, elevation, roll))
    f = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    r = np.array([-math.sin(az), math.cos(az), 0.0])
    u = np.cross(f, r)
    if rl:
        r, u = r * math.cos(rl) + u * math.sin(rl), -r * math.sin(rl) + u * math.cos(rl)
    return np.stack([r, u, f])


class Scene:
    def __init__(self, mesh, azimuth=-90.0, elevation=10.0, roll=0.0, paper="A3", orientation="portrait",
                 margin=20.0, light="front"):
        import trimesh
        R = camera_basis(azimuth, elevation, roll)
        self.R = R
        self.V = np.asarray(mesh.vertices, dtype=np.float64) @ R.T
        self.F = np.asarray(mesh.faces, dtype=np.int64)
        self.tm = trimesh.Trimesh(self.V, self.F, process=False)
        self.tm.metadata.update(mesh.metadata)
        self.FN = self.tm.face_normals
        self.VN = self.tm.vertex_normals
        self.front = self.FN[:, 2] > 0
        L = np.asarray(LIGHTS.get(light, LIGHTS["front"]), dtype=float)
        self.L = L / np.linalg.norm(L)
        self.vtone = np.clip(self.VN @ self.L, 0.0, 1.0)
        self.ftone = np.clip(self.FN @ self.L, 0.0, 1.0)
        x, y = self.V[:, 0], self.V[:, 1]
        self.sheet = sheet(paper, orientation)
        self.s, self.tx, self.ty = fit((x.min(), -y.max(), x.max(), -y.min()), paper, orientation, margin)
        self.P = self.to_paper(self.V)
        ext = np.ptp(self.V, axis=0)
        self.diag = float(np.linalg.norm(ext)) or 1.0
        self.eps = self.diag * 4e-3              # depth tolerance of the visibility test
        self.z_top = float(self.V[:, 2].max()) + 0.05 * self.diag
        self.rays = 0

    # ------------------------------------------------------------------ coordinates
    def to_paper(self, cam: np.ndarray) -> np.ndarray:
        cam = np.asarray(cam, dtype=float)
        return np.column_stack([self.s * cam[..., 0] + self.tx, -self.s * cam[..., 1] + self.ty])

    def to_cam_xy(self, paper: np.ndarray) -> np.ndarray:
        paper = np.asarray(paper, dtype=float).reshape(-1, 2)
        return np.column_stack([(paper[:, 0] - self.tx) / self.s, -(paper[:, 1] - self.ty) / self.s])

    def mm(self, cam_length: float) -> float:
        return cam_length * self.s

    def cam(self, mm_length: float) -> float:
        return mm_length / self.s

    # ------------------------------------------------------------------ visibility
    def first_hits(self, paper: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """For paper points: (depth z of the first surface hit, face index), NaN / -1 where nothing is hit."""
        xy = self.to_cam_xy(paper)
        n = len(xy)
        z = np.full(n, np.nan)
        face = np.full(n, -1, dtype=np.int64)
        if n == 0:
            return z, face
        origins = np.column_stack([xy, np.full(n, self.z_top)])
        dirs = np.tile([0.0, 0.0, -1.0], (n, 1))
        self.rays += n
        chunk = 200_000
        for a in range(0, n, chunk):
            loc, ri, fi = self.tm.ray.intersects_location(origins[a:a + chunk], dirs[a:a + chunk],
                                                          multiple_hits=False)
            if len(ri):
                z[a + ri] = loc[:, 2]
                face[a + ri] = fi
        return z, face

    def visible_points(self, cam3: np.ndarray) -> np.ndarray:
        """Points on the model (camera space) that nothing hides. A ray that misses grazes the outline: visible."""
        cam3 = np.asarray(cam3, dtype=float).reshape(-1, 3)
        z, _ = self.first_hits(self.to_paper(cam3))
        return np.isnan(z) | (cam3[:, 2] >= z - self.eps)

    def surface_at(self, paper: np.ndarray) -> np.ndarray:
        """Face seen at each paper point (-1: background)."""
        return self.first_hits(paper)[1]

    # ------------------------------------------------------------------ cutting lines by visibility
    def clip_polylines(self, polylines_cam: list, step_mm: float = 0.3, keep_seed: int | None = None) -> list:
        """Split 3D polylines (camera space) into their visible runs, in paper mm. Every polyline is sampled every
        `step_mm` on paper and all samples go through one batch of rays."""
        dense, owner = [], []
        for k, pl in enumerate(polylines_cam):
            pl = np.asarray(pl, dtype=float)
            if len(pl) < 2:
                continue
            d = densify(pl, self.cam(step_mm))
            dense.append(d)
            owner.append(np.full(len(d), k))
        if not dense:
            return []
        pts = np.concatenate(dense)
        vis = self.visible_points(pts)
        paper = self.to_paper(pts)
        out, i = [], 0
        for d in dense:
            n = len(d)
            out += runs(paper[i:i + n], vis[i:i + n])
            i += n
        return out

    def clip_on_surface(self, polylines_paper: list, step_mm: float = 0.3, keep=None) -> list:
        """Split 2D lines drawn on paper into the stretches that lie over the model (optionally where
        keep(face indices, paper points) says so, e.g. dark enough)."""
        dense = [densify(np.asarray(pl, dtype=float), step_mm) for pl in polylines_paper if len(pl) >= 2]
        if not dense:
            return []
        pts = np.concatenate(dense)
        face = self.surface_at(pts)
        ok = face >= 0
        if keep is not None:
            ok &= keep(face, pts)
        out, i = [], 0
        for d in dense:
            out += runs(d, ok[i:i + len(d)])
            i += len(d)
        return out


def densify(pl: np.ndarray, step: float) -> np.ndarray:
    """Insert points so no segment is longer than `step` (in the polyline's own units)."""
    seg = np.diff(pl, axis=0)
    ln = np.linalg.norm(seg[:, :2] if pl.shape[1] == 3 else seg, axis=1)
    n = np.maximum(1, np.ceil(ln / max(step, 1e-12)).astype(int))
    if (n == 1).all():
        return pl
    parts = [pl[:1]]
    for a, s, k in zip(pl[:-1], seg, n):
        t = (np.arange(1, k + 1) / k)[:, None]
        parts.append(a + t * s)
    return np.concatenate(parts)


def runs(pts: np.ndarray, ok: np.ndarray) -> list:
    """Consecutive True stretches of a polyline (at least two points), as lists of (x, y)."""
    out = []
    if len(pts) < 2:
        return out
    idx = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(np.int8), [0]])))
    for a, b in zip(idx[0::2], idx[1::2]):
        if b - a >= 2:
            out.append([tuple(p) for p in pts[a:b, :2]])
    return out


# ----------------------------------------------------------------------------- feature lines

def silhouette_edges(scene: Scene, method: str = "contour", grazing: float = 0.1) -> np.ndarray:
    """(k, 2) vertex pairs. contour: edges between a front and a back face, plus open boundary edges
    (v2.1's true silhouette). grazing: every edge of faces seen nearly edge-on, |n·v| < threshold (v5)."""
    import trimesh
    tm = scene.tm
    if method == "grazing":
        faces = scene.F[np.abs(scene.FN[:, 2]) < grazing]
        e = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
        return np.unique(e, axis=0) if len(e) else np.zeros((0, 2), dtype=np.int64)
    pairs = tm.face_adjacency
    edges = tm.face_adjacency_edges
    sil = edges[scene.front[pairs[:, 0]] != scene.front[pairs[:, 1]]] if len(pairs) else np.zeros((0, 2), int)
    boundary = tm.edges_sorted[trimesh.grouping.group_rows(tm.edges_sorted, require_count=1)]
    return np.concatenate([sil, boundary]) if len(boundary) else sil


def crease_edges(scene: Scene, angle_deg: float = 30.0) -> np.ndarray:
    tm = scene.tm
    if len(tm.face_adjacency) == 0:
        return np.zeros((0, 2), dtype=np.int64)
    pairs = tm.face_adjacency
    sharp = tm.face_adjacency_angles > math.radians(angle_deg)
    from atlas_toolkit.geom.surface import native_edge_mask
    sharp &= native_edge_mask(tm, tm.face_adjacency_edges, pairs)
    seen = scene.front[pairs[:, 0]] | scene.front[pairs[:, 1]]
    return tm.face_adjacency_edges[sharp & seen]


def edge_polylines(scene: Scene, edges: np.ndarray) -> list:
    """Chain edges sharing vertices into 3D polylines (camera space)."""
    if len(edges) == 0:
        return []
    import networkx as nx
    g = nx.Graph()
    g.add_edges_from(map(tuple, edges))
    out = []
    for comp in nx.connected_components(g):
        sub = g.subgraph(comp)
        for path in trails(sub):
            out.append(scene.V[path])
    return out
