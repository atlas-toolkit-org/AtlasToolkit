"""Local solid bridges from final cavity interfaces to safe fragment interiors.

The extraction itself is unchanged. This second, independently seeded pass keeps each source interface,
tests it against the FINAL cavity body, builds embedded joints, and boolean-fuses the complete assembly.
Organic forks share one trunk and end inside their own fragment; centreline offsets have a hard envelope.
"""
from dataclasses import dataclass
import manifold3d as mf
import numpy as np
import trimesh

from . import mesh_surface_eject as se
from . import branching
from atlas_toolkit.geom.solid_connectors import (
    union, interior_candidates, edge_clearance, from_arrays, beam, stabilize, curve, tube,
)


def selected_indices(extraction, p):
    """Exact rounded count, with a stable ranking independent of extraction and connector-shape RNGs."""
    count = len(extraction.parts)
    percent = float(p.get("connector_percent", 100.)) if p.get("connectors") else 0.
    wanted = min(count, max(0, int(np.floor(count * percent / 100. + .5))))
    if wanted == count:
        return list(range(count))
    if wanted == 0:
        return []
    selection = p.get("connector_selection", "random")
    if selection == "random":
        rng = np.random.default_rng(np.random.SeedSequence([int(p.get("connector_seed", 0)), 8034]))
        ranked = rng.permutation(count)
    else:
        volumes = np.asarray([row["volume"] for row in extraction.records])
        ranked = np.argsort(-volumes if selection == "largest" else volumes, kind="stable")
    return sorted(int(index) for index in ranked[:wanted])


def cavity_attachment(body_mesh, body_query, interface, target, width, radius, eps):
    """Choose a point on THIS cut interface which still meets the final body, and embed into that body."""
    if not len(interface):
        raise ValueError("The fragment has no cavity interface to attach to.")
    cap = trimesh.Trimesh(interface.reshape(-1, 3), np.arange(interface.size // 3).reshape(-1, 3), process=True)
    near, _, fid = trimesh.proximity.closest_point(cap, [target])
    ids = np.argsort(-cap.area_faces, kind="stable")[:96]
    points = np.vstack((near, cap.triangles_center[ids]))
    normals = np.vstack((cap.face_normals[fid], cap.face_normals[ids]))
    _, distances, body_faces = body_query.on_surface(points)
    margins = edge_clearance(cap, points)
    valid = ((distances <= eps * 10) & (margins > eps * 40)
             & (np.einsum("ij,ij->i", body_mesh.face_normals[body_faces], normals) < -.5))
    choices = np.flatnonzero(valid)
    if not len(choices):
        raise ValueError("Later cuts removed this fragment's usable cavity surface. Increase Footprint spacing or change Seed.")
    costs = np.linalg.norm(points[choices] - target, axis=1) - .1 * margins[choices]
    for i in choices[np.argsort(costs, kind="stable")]:
        distance = min(radius * .7, width * .04, margins[i] * .3)
        for _ in range(10):
            inside = points[i] + normals[i] * distance
            if distance > eps * 10 and body_mesh.contains([inside])[0]:
                return points[i], normals[i], inside, float(margins[i])
            distance *= .5
    raise ValueError("Cavity wall is too thin for a reliable embedded joint. Reduce extraction depth.")


@dataclass
class Connections:
    combined: object
    connectors: list
    records: list
    precision: float = 0.


def build(extraction, p, height, ctx=None):
    """Every returned connector overlaps the final cavity body and its own fragment by positive volume."""
    selected = selected_indices(extraction, p)
    if not selected:
        return Connections(None, [], [])
    eps = height * 1e-7
    body_mesh = se.to_mesh(extraction.body)
    body_query = trimesh.proximity.ProximityQuery(body_mesh)
    all_parts = union(extraction.parts)
    segments = min(48, int(p["segments"]))
    connectors, records = [], []
    precision = 0.
    for ordinal, index in enumerate(selected):
        part, row, interface = extraction.parts[index], extraction.records[index], extraction.interfaces[index]
        if ctx:
            ctx.progress(.80 + .035 * ordinal / len(selected), f"Connecting {ordinal + 1} / {len(selected)} · fragment {index + 1}")
        rng = np.random.default_rng(np.random.SeedSequence([int(p["seed"]), index, 8033]))
        requested_radius = rng.uniform(p["connector_diameter_min"], p["connector_diameter_max"]) * height / 200
        delta = np.asarray(row["normal"]) * row["travel"]
        rest = se.to_mesh(part.translate(-delta))
        centre, clearance, pool, margins = interior_candidates(rest, row["width"], eps)
        root, root_normal, start, root_margin = cavity_attachment(
            body_mesh, body_query, interface, centre, row["width"], requested_radius, eps)
        finish = centre + delta
        axis = finish - start
        length = np.linalg.norm(axis)
        axis /= length
        style = p["connector_style"]
        taper = float(p["connector_taper"]) if style == "organic" else 1.
        factor = np.sqrt(2.) if style == "bar" else 1.
        radius = min(requested_radius, clearance * .65 / (factor * taper), root_margin * .6 / factor,
                     row["width"] * .16, length * (.12 if style == "organic" else .3))
        if radius < eps * 40:
            raise ValueError(f"Fragment {index + 1} is too thin for a safe connector. Increase depth or reduce thickness.")
        extent = row["width"] * float(p["connector_extent"]) / 100 if style == "organic" else 0.
        phase = float(rng.uniform(0., 2 * np.pi))
        other_parts = all_parts - part

        def valid(candidate):
            attached_body, attached_part = candidate ^ extraction.body, candidate ^ part
            if attached_body.volume() <= eps ** 3 or attached_part.volume() <= eps ** 3:
                return False
            # Allow contact within the original cavity, but not a second unintended body attachment far away.
            contact_vertices = se.to_mesh(attached_body).vertices
            if np.linalg.norm(contact_vertices - root, axis=1).max() > row["width"] * 1.1 + row["depth"]:
                return False
            return (candidate ^ other_parts).volume() <= eps ** 3

        requested_branches = branching.requested_count(p, index)
        effective_scale = 1.
        for effective_scale in (1., .5, 0.):
            if style == "organic":
                path = curve(start, finish, start, axis, extent, p["connector_curve"] * effective_scale,
                             p["connector_irregularity"] * effective_scale, phase)
                candidate = tube(path, radius, taper, p["connector_irregularity"] * effective_scale, phase, segments)
            else:
                path = np.array([start, finish])
                candidate = beam(start, finish, radius, style, segments)
            if valid(candidate):
                break
        else:
            raise ValueError(f"Connector for fragment {index + 1} is obstructed or cannot reach its cavity. "
                             "Increase Footprint spacing, reduce ejection distance or change Seed; no output was published.")
        paths = [path]
        branch_solids = [candidate]
        levels, path_radii = [0], [radius]
        origins = []
        achieved = 0
        if requested_branches and extent > eps * 10:
            placement = p.get("connector_branch_placement", "split")
            max_levels = int(p.get("connector_branch_levels", 1))
            ratio = float(p.get("connector_branch_thickness", .68))
            density = min(1., np.sqrt(4. / requested_branches))
            legacy = placement == "split" and max_levels == 1 and requested_branches <= 4
            if requested_branches > 4:
                extra_pool, extra_margins = branching.extra_tips(rest, centre, row["normal"], requested_branches, eps)
                pool, margins = np.vstack((pool, extra_pool)), np.r_[margins, extra_margins]
            frame = se.basis(axis)
            lateral = pool - centre - np.outer((pool - centre) @ axis, axis)
            spread = min(extent, row["width"] * float(p["connector_spread"]) / 100)
            within_spread = np.linalg.norm(lateral, axis=1) <= spread + eps
            used = [finish]
            intervals = {}
            plan = branching.origin_plan(p, index, requested_branches)
            fixed_faces = extraction.body.num_tri() + sum(part.num_tri() for part in extraction.parts) + sum(s.num_tri() for s in connectors)
            for branch_index, spec in enumerate(plan):
                if ctx:
                    ctx.check_cancel()
                    if branch_index % 4 == 0 or branch_index + 1 == requested_branches:
                        ctx.progress(.80 + .035 * (ordinal + branch_index / requested_branches) / len(selected),
                                     f"Fragment {index + 1}: branch {branch_index + 1} / {requested_branches}")
                if spread <= eps:
                    break
                # Keep roots on the main trunk regularly, with smaller generations between them.
                parents = [j for j, level in enumerate(levels) if 0 < level < max_levels]
                parent = parents[min(int(spec["parent_draw"] * len(parents)), len(parents) - 1)] if parents and branch_index % 4 else 0
                parent_path = paths[parent]
                if placement == "split":
                    fork_index = int(np.clip(round(spec["fraction"] * (len(parent_path) - 1)), 1, len(parent_path) - 2))
                    junction, fraction = parent_path[fork_index], fork_index / (len(parent_path) - 1)
                    distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(parent_path, axis=0), axis=1))]
                    path_fraction = float(distances[fork_index] / distances[-1])
                else:
                    if parent not in intervals:
                        intervals[parent] = branching.visible_interval(parent_path, body_mesh, se.to_mesh(part))
                    lo, hi = intervals[parent]
                    path_fraction = lo + (hi - lo) * spec["fraction"]
                    junction, fraction = branching.path_point(parent_path, path_fraction)
                branch_radius = min(path_radii[parent] * (1 - (1 - taper) * fraction) * ratio,
                                    radius * ratio * density / np.sqrt(levels[parent] + 1))
                tip_radius = branch_radius * taper
                eligible = within_spread & (margins >= tip_radius * 1.6)
                endpoints = pool[eligible] + delta
                if not len(endpoints) or branch_radius < eps * 40:
                    continue
                if legacy:
                    azimuth = phase + 2 * np.pi * branch_index / requested_branches
                    reach = spread
                else:
                    azimuth = phase + branch_index * np.pi * (3 - np.sqrt(5)) + spec["angle_jitter"]
                    reach = spread * np.sqrt((branch_index + .5) / requested_branches)
                desired = finish + reach * (np.cos(azimuth) * frame[:, 0] + np.sin(azimuth) * frame[:, 1])
                spacing = max(tip_radius * 3., spread * min(.25, .9 / np.sqrt(requested_branches)))
                for j in np.argsort(np.linalg.norm(endpoints - desired, axis=1), kind="stable")[:32 if not legacy else 24]:
                    if ctx:
                        ctx.check_cancel()
                    tip = endpoints[j]
                    if min(np.linalg.norm(tip - point) for point in used) < spacing:
                        continue
                    local_radius = min(branch_radius, np.linalg.norm(tip - junction) * .12)
                    if local_radius < eps * 40:
                        continue
                    branch_path = curve(junction, tip, start, axis, extent, p["connector_curve"] * effective_scale * .7,
                                        p["connector_irregularity"] * effective_scale, azimuth)
                    branch = tube(branch_path, local_radius, taper, p["connector_irregularity"] * effective_scale, azimuth, segments)
                    joined = candidate + branch
                    if legacy:
                        fits = len(joined.decompose()) == 1 and valid(joined)
                    else:
                        contact = branch ^ extraction.body
                        local_contact = contact.is_empty() or np.linalg.norm(se.to_mesh(contact).vertices - root, axis=1).max() <= row["width"] * 1.1 + row["depth"]
                        fits = (local_contact and (branch ^ part).volume() > eps ** 3
                                and (branch ^ candidate).volume() > eps ** 3
                                and (other_parts.is_empty() or (branch ^ other_parts).volume() <= eps ** 3)
                                and len(joined.decompose()) == 1)
                    if fits:
                        if fixed_faces + joined.num_tri() > p["max_faces"]:
                            raise ValueError("Branches exceed Maximum faces. Reduce Pieces / Extra branches / Curved shape segments.")
                        candidate = joined
                        branch_solids.append(branch)
                        paths.append(branch_path)
                        levels.append(levels[parent] + 1)
                        path_radii.append(local_radius)
                        origins.append({"zone": spec["zone"], "fraction": spec["fraction"], "path_fraction": float(path_fraction),
                                        "parent": int(parent), "level": int(levels[-1])})
                        used.append(tip)
                        achieved += 1
                        break
            if not legacy:
                # Optional twigs must not make an otherwise sound attachment impossible to export.
                # Removing the newest leaf preserves all parent relationships and the main solid bridge.
                while len(branch_solids) > 1:
                    if ctx:
                        ctx.check_cancel()
                    try:
                        stable, adjustment = stabilize(candidate, height)
                        candidate = stable
                        precision = max(precision, adjustment)
                        stabilize(union([extraction.body, part, candidate]), height)
                        break
                    except ValueError:
                        branch_solids.pop()
                        paths.pop()
                        levels.pop()
                        path_radii.pop()
                        origins.pop()
                        achieved -= 1
                        candidate = union(branch_solids)
        if candidate.status() != mf.Error.NoError or len(candidate.decompose()) != 1:
            raise ValueError("Connector union did not form one valid solid; no output was published.")
        connectors.append(candidate)
        lateral_max = max(float(np.linalg.norm(path - start - np.outer((path - start) @ axis, axis), axis=1).max()) for path in paths)
        records.append({"fragment_index": index, "cavity": root.tolist(), "root_inside": start.tolist(), "fragment_inside": finish.tolist(),
                        "requested_diameter": requested_radius * 2, "diameter": radius * 2,
                        "clamped": bool(radius < requested_radius * .999), "branches": achieved,
                        "requested_branches": requested_branches, "extent": extent, "lateral_max": lateral_max,
                        "curve_scale": effective_scale, "paths": [path.tolist() for path in paths],
                        "branch_origins": origins, "branch_levels": max(levels),
                        "branch_count_mode": p.get("connector_branch_count", "fixed"),
                        "branch_placement": p.get("connector_branch_placement", "split")})
        if sum(s.num_tri() for s in connectors) + sum(s.num_tri() for s in extraction.parts) + extraction.body.num_tri() > p["max_faces"]:
            raise ValueError("Connectors exceed Maximum faces. Reduce Pieces / Extra branches / Curved shape segments.")
    if ctx:
        ctx.check_cancel()
    joined = union([extraction.body, *(extraction.parts[index] for index in selected), *connectors])
    # Check the selected assembly on its own: loose components are legitimate only for unselected fragments.
    if len(joined.decompose()) > len(extraction.body.decompose()):
        raise ValueError("A fragment remained unattached after union; no output was published.")
    loose = [part for index, part in enumerate(extraction.parts) if index not in selected]
    combined = union([joined, *loose]) if loose else joined
    expected_components = len(joined.decompose()) + len(loose)
    if (combined.status() != mf.Error.NoError or combined.is_empty() or combined.num_tri() > p["max_faces"]
            or len(combined.decompose()) != expected_components):
        raise ValueError("Connector assembly lost a selected joint or an unselected floating piece.")
    combined, adjustment = stabilize(combined, height)
    return Connections(combined, connectors, records, max(precision, adjustment))
