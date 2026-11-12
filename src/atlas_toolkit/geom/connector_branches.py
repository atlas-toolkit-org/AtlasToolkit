"""Deterministic organic branch counts, origin zones and safe interior tip sampling."""
import numpy as np
import trimesh


ZONES = {"beginning": (.05, .30), "middle": (.35, .65), "end": (.70, .95), "random": (.05, .95)}


def requested_count(p, fragment_index):
    if p["connector_style"] != "organic":
        return 0
    if p.get("connector_branch_count", "fixed") != "random":
        return int(p["connector_branches"])
    rng = np.random.default_rng(np.random.SeedSequence([
        int(p["seed"]), int(p.get("connector_branch_seed", 0)), int(fragment_index), 8035]))
    return int(rng.integers(int(p["connector_branches_min"]), int(p["connector_branches_max"]) + 1))


def origin_plan(p, fragment_index, count):
    """Separate stream: changing counts or percentage does not alter the main stem or selection."""
    rng = np.random.default_rng(np.random.SeedSequence([
        int(p["seed"]), int(p.get("connector_branch_seed", 0)), int(fragment_index), 8036]))
    placement = p.get("connector_branch_placement", "split")
    zones = np.array(["beginning", "middle", "end"])[rng.permutation(3)]
    plan = []
    for index in range(count):
        zone = str(zones[index % 3]) if placement == "mixed" else placement
        fraction = float(p["connector_fork"]) / 100 if zone == "split" else float(rng.uniform(*ZONES[zone]))
        plan.append({"zone": zone, "fraction": fraction, "parent_draw": float(rng.random()),
                     "angle_jitter": float(rng.uniform(-.22, .22))})
    return plan


def path_point(path, fraction):
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    target = float(fraction) * distance[-1]
    index = int(np.clip(np.searchsorted(distance, target, side="right") - 1, 0, len(path) - 2))
    blend = (target - distance[index]) / max(distance[index + 1] - distance[index], 1e-30)
    return path[index] * (1 - blend) + path[index + 1] * blend, (index + blend) / (len(path) - 1)


def visible_interval(path, body_mesh, fragment_mesh):
    outside = ~(body_mesh.contains(path) | fragment_mesh.contains(path))
    ids = np.flatnonzero(outside)
    if len(ids) < 2:
        return 0., 1.
    # Use the largest visible run, avoiding embedded ends and unrelated intervals on concave parents.
    groups = np.split(ids, np.flatnonzero(np.diff(ids) > 1) + 1)
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    group = max(groups, key=lambda g: distance[g[-1]] - distance[g[0]])
    return float(distance[group[0]] / distance[-1]), float(distance[group[-1]] / distance[-1])


def extra_tips(mesh, centre, normal, count, eps):
    """Low-discrepancy volume samples in the fragment-aligned box; never move the main attachment."""
    from scipy.stats import qmc
    from .solid_connectors import basis
    frame = basis(normal)
    local = (mesh.vertices - centre) @ frame
    lo, hi = local.min(0), local.max(0)
    samples = qmc.Halton(3, scramble=False).random(max(1024, 40 * count))
    points = (lo + samples * (hi - lo)) @ frame.T + centre
    points = points[mesh.contains(points)]
    if not len(points):
        return np.empty((0, 3)), np.empty(0)
    _, margins, _ = trimesh.proximity.closest_point(mesh, points)
    return points[margins > eps * 20], margins[margins > eps * 20]
