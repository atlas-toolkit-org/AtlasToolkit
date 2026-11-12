"""Exact surface-volume extraction. GUI-free, deterministic, no remeshing on closed inputs.

Manifold's Split returns both sides of the same cut. Original triangle provenance identifies the actual
outer skin, supplies its area-weighted normal and prevents a painted selection from leaking onto other faces.
"""
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json

import manifold3d as mf
import numpy as np
import trimesh


def fingerprint(mesh):
    digest = hashlib.sha256()
    digest.update(np.asarray(mesh.vertices, dtype="<f8").tobytes())
    digest.update(np.asarray(mesh.faces, dtype="<i8").tobytes())
    return digest.hexdigest()


def encode_selection(mesh, selected):
    ids = np.flatnonzero(selected)
    starts = ids[np.r_[True, np.diff(ids) != 1]] if len(ids) else []
    ends = ids[np.r_[np.diff(ids) != 1, True]] if len(ids) else []
    return json.dumps({"format": "atlas.surface-selection.v1", "mesh": fingerprint(mesh),
                       "faces": len(mesh.faces), "ranges": [[int(a), int(b)] for a, b in zip(starts, ends)]},
                      separators=(",", ":"))


def decode_selection(mesh, text):
    try:
        data = json.loads(text)
        if data.get("format") != "atlas.surface-selection.v1":
            raise ValueError("Unsupported selection format.")
        if data.get("mesh") != fingerprint(mesh) or data.get("faces") != len(mesh.faces):
            raise ValueError("Painted selection belongs to a different mesh. Open Selection and repaint this input.")
        mask = np.zeros(len(mesh.faces), dtype=bool)
        for a, b in data["ranges"]:
            if type(a) is not int or type(b) is not int or not 0 <= a <= b < len(mask):
                raise ValueError("Invalid selected face range.")
            mask[a:b + 1] = True
        if not mask.any():
            raise ValueError("Painted selection is empty. Paint an area before ejecting.")
        return mask
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid selection JSON. Paint an area or load an exported selection file.") from exc


def validate(p):
    for key in ("size", "depth", "distance"):
        if p[key + "_min"] > p[key + "_max"]:
            raise ValueError(f"{key.title()} minimum must not exceed its maximum.")
    if p["shape"] in ("cube", "sphere") and p["depth_max"] >= p["size_min"]:
        raise ValueError("Cube / Sphere require Depth maximum < Size minimum. Use Box / Ellipsoid / Cylinder for independent deep sampling.")
    if p["region"] == "band" and p["band_from"] >= p["band_to"]:
        raise ValueError("Band from must be below Band to.")
    if p.get("connectors") and p.get("connector_percent", 100.) > 0:
        if p["connector_diameter_min"] > p["connector_diameter_max"]:
            raise ValueError("Connector thickness minimum must not exceed its maximum.")
        if not p["fully_detach"]:
            raise ValueError("Enable Fully detach when connecting fragments to cavities.")
        if (p.get("connector_style") == "organic" and p.get("connector_branch_count") == "random"
                and p["connector_branches_min"] > p["connector_branches_max"]):
            raise ValueError("Branches minimum must not exceed Branches maximum.")


def solid(mesh, selected=None):
    """Keep source face IDs even on coplanar faces; this is important for painted masks."""
    first = mf.Manifold.reserve_ids(2)
    ident = (first, first + 1)
    v = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
    f = np.ascontiguousarray(mesh.faces, dtype=np.uint64)
    flags = np.ones(len(f), bool) if selected is None else np.asarray(selected, bool)
    order = np.r_[np.flatnonzero(flags), np.flatnonzero(~flags)]
    boundary = int(flags.sum()) * 3
    # Separate runs stop Manifold from merging coplanar selected/unselected triangles.
    data = mf.Mesh64(v, f[order], run_index=np.array([0, boundary, f.size], np.uint64),
                     run_original_id=np.array(ident, np.uint32), face_id=order.astype(np.uint64))
    body = mf.Manifold(data)
    if body.status() != mf.Error.NoError or body.is_empty() or body.volume() <= 0:
        raise ValueError("Input must be an outward-oriented closed solid. Repair it first or enable Repair by resampling.")
    return body, ident


def to_mesh(body):
    data = body.to_mesh64()
    return trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3], np.asarray(data.tri_verts), process=False)


def source_skin(body, ident, original=True):
    data = body.to_mesh64()
    selected = np.zeros(len(data.tri_verts), bool)
    runs = np.asarray(data.run_index) // 3
    for i, original_id in enumerate(data.run_original_id):
        if original_id in ident:
            selected[int(runs[i]):int(runs[i + 1])] = True
    if not original:
        selected = ~selected
    tri = np.asarray(data.vert_properties)[:, :3][np.asarray(data.tri_verts)[selected]]
    ids = np.asarray(data.face_id, dtype=np.int64)[selected]
    return tri, ids


def basis(normal):
    n = np.array(normal, dtype=float, copy=True)
    n /= np.linalg.norm(n)
    ref = np.eye(3)[np.argmin(np.abs(n))]
    u = np.cross(ref, n)
    u /= np.linalg.norm(u)
    return np.column_stack((u, np.cross(n, u), n))


def sampler(shape, width, depth, point, normal, segments=32):
    """Closed sampler; the deepest local Z is exactly -depth. No rotation of exported fragments."""
    if shape == "cube":
        body = mf.Manifold.cube([width] * 3, True).translate([0, 0, width / 2 - depth])
    elif shape == "box":
        height = depth + width / 2
        body = mf.Manifold.cube([width, width, height], True).translate([0, 0, height / 2 - depth])
    elif shape == "sphere":
        body = mf.Manifold.sphere(width / 2, segments).translate([0, 0, width / 2 - depth])
    elif shape == "ellipsoid":
        body = mf.Manifold.sphere(1., segments).scale([width / 2, width / 2, depth])
    elif shape == "cylinder":
        body = mf.Manifold.cylinder(depth + width / 2, width / 2, circular_segments=segments).translate([0, 0, -depth])
    else:
        raise ValueError("Unknown sampler shape.")
    return body.transform(np.column_stack((basis(normal), np.asarray(point))))


def detached(piece, obstacle, point, normal, gap, depth, eps):
    """Certify clearance to the original body AND all previously moved pieces.

    Try the local lift first. If a concavity obstructs it, a support plane gives a finite guaranteed endpoint.
    Distances are lower bounds on clear space, rather than undocumented upper bounds on total travel.
    """
    vertices = to_mesh(piece).vertices
    travel = max(depth, float(np.dot(point, normal) - (vertices @ normal).min())) + gap + eps
    moved = piece.translate(normal * travel)

    def clear(candidate):
        return ((candidate ^ obstacle).volume() <= eps ** 3
                and candidate.min_gap(obstacle, gap + 2 * eps) >= gap + eps * .5)

    if not clear(moved):
        # Every obstacle vertex lies behind this plane, including detached pieces on concave forms.
        support = float((to_mesh(obstacle).vertices @ normal).max())
        travel = max(travel, support - float((vertices @ normal).min()) + gap + 4 * eps)
        moved = piece.translate(normal * travel)
        if not clear(moved):
            raise ValueError("Could not certify fragment clearance. Recenter or repair the input model.")
    return moved, travel


@dataclass
class Extraction:
    body: object
    parts: list
    records: list
    rejected: dict
    attempts: int
    original_volume: float
    interfaces: list = field(default_factory=list)


def extract(mesh, p, selected=None, ctx=None):
    validate(p)
    budget = int(p["max_faces"])
    if len(mesh.faces) > budget:
        raise ValueError("Input exceeds Maximum faces. Simplify the model or raise the limit.")
    original, ident = solid(mesh, selected)
    remaining, obstacle = original, original
    volume = original.volume()
    height = float(mesh.extents[2])
    if height <= 0:
        raise ValueError("Model has no height in the selected frame.")
    scale, eps = height / 100, height * 1e-7
    rng = np.random.default_rng(int(p["seed"]))
    allowed = np.ones(len(mesh.faces), bool) if selected is None else np.asarray(selected, bool)
    if p["region"] == "paint" and (selected is None or not allowed.any()):
        raise ValueError("Paint an area before ejecting.")
    region = None
    low, high = mesh.bounds[:, 2]
    if p["region"] == "band":
        low, high = low + height * p["band_from"] / 100, low + height * p["band_to"] / 100
        allowed &= (mesh.triangles[:, :, 2].max(1) >= low) & (mesh.triangles[:, :, 2].min(1) <= high)
        span = mesh.extents + height * 2
        region = mf.Manifold.cube([span[0], span[1], high - low], True).translate([
            *mesh.bounds.mean(0)[:2], (low + high) / 2])
    areas = np.where(allowed, mesh.area_faces, 0.)
    if areas.sum() <= 0:
        raise ValueError("The selected region contains no surface area.")
    cumulative = np.cumsum(areas) / areas.sum()
    parts, records, anchors, interfaces = [], [], [], []
    rejected = Counter()
    wanted = int(p["count"])
    limit = max(200, wanted * 35)
    for attempt in range(limit):
        if ctx:
            ctx.progress(.15 + .65 * len(parts) / wanted, f"Extracting {len(parts)} / {wanted} · attempt {attempt + 1}")
        fi = min(int(np.searchsorted(cumulative, rng.random(), side="right")), len(areas) - 1)
        u, v = rng.random(2)
        root = np.sqrt(u)
        point = np.array([1 - root, root * (1 - v), root * v]) @ mesh.triangles[fi]
        width = rng.uniform(p["size_min"], p["size_max"]) * scale
        depth = rng.uniform(p["depth_min"], p["depth_max"]) * scale
        gap = rng.uniform(p["distance_min"], p["distance_max"]) * scale
        # Circumradius of the square footprint; round samplers use their true radius.
        radius = width * (.5 if p["shape"] in ("sphere", "ellipsoid", "cylinder") else np.sqrt(.5))
        if point[2] < low or point[2] > high:
            rejected["outside band"] += 1
            continue
        if p["spacing"] > 0 and any(np.linalg.norm(point - q) < radius + r + p["spacing"] * scale for q, r in anchors):
            rejected["spacing"] += 1
            continue
        cutter = sampler(p["shape"], width, depth, point, mesh.face_normals[fi], int(p["segments"]))
        if region is not None:
            cutter = cutter ^ region
        piece, body = remaining.split(cutter)
        if piece.is_empty() or piece.volume() <= volume * 1e-10:
            rejected["already removed or negligible"] += 1
            continue
        if body.is_empty() or body.volume() <= volume * 1e-6:
            rejected["would remove entire body"] += 1
            continue
        skin, source_ids = source_skin(piece, ident)
        if not len(skin):
            rejected["no original surface"] += 1
            continue
        if p["region"] == "paint" and not allowed[source_ids].all():
            rejected["crosses painted boundary"] += 1
            continue
        vectors = np.cross(skin[:, 1] - skin[:, 0], skin[:, 2] - skin[:, 0])
        summed = vectors.sum(0)
        length = np.linalg.norm(summed)
        if (length < .15 * np.linalg.norm(vectors, axis=1).sum()
                or np.any(vectors @ mesh.face_normals[fi] < -eps ** 2)):
            rejected["reaches opposite skin"] += 1
            continue
        if len(piece.decompose()) != 1:
            rejected["disconnected sample"] += 1
            continue
        normal = summed / length
        if p["fully_detach"]:
            moved, travel = detached(piece, obstacle, point, normal, gap, depth, eps)
        else:
            travel = gap
            moved = piece.translate(normal * travel)
        if body.num_tri() + sum(part.num_tri() for part in parts) + moved.num_tri() > budget:
            raise ValueError("Extraction exceeds Maximum faces. Reduce Pieces / sampler segments or raise the limit.")
        remaining = body
        parts.append(moved)
        # A union is used only for collision queries. Exported fragments are concatenated without welding.
        if p["fully_detach"]:
            obstacle = obstacle + moved
        anchors.append((point, radius))
        records.append({"anchor": point.tolist(), "normal": normal.tolist(), "width": width,
                        "depth": depth, "gap": gap, "travel": travel, "volume": piece.volume(),
                        "skin_area": np.linalg.norm(vectors, axis=1).sum() / 2,
                        "source_faces": np.unique(source_ids).tolist()})
        if p.get("connectors") and p.get("connector_percent", 100.) > 0:
            interfaces.append(source_skin(piece, ident, original=False)[0])
        if len(parts) == wanted:
            break
    if not parts:
        reasons = ", ".join(f"{k}: {v}" for k, v in rejected.items())
        raise ValueError("No surface pieces could be extracted. Enlarge the painted area, reduce size / depth / wall, or change Seed. " + reasons)
    return Extraction(remaining, parts, records, dict(rejected), attempt + 1, volume, interfaces)
