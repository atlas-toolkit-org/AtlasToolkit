"""Surface algorithms and validation, independent of Qt and publication."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import tempfile
import numpy as np

from atlas_toolkit.core.errors import ToolError

from atlas_toolkit.geom.surface import Surface, from_arrays, from_mesh, triangulate
from . import backend


class SubContext:
    def __init__(self, ctx, start, span):
        self.ctx, self.start, self.span = ctx, start, span
    def progress(self, fraction, message=''):
        self.ctx.progress(self.start + self.span * fraction, message)
    def check_cancel(self):
        self.ctx.check_cancel()


def _needs_repair(mesh):
    from atlas_toolkit.geom import repair as R
    e = R.NeedsRepair(R.diagnose(mesh), "manifold", tool_name="Remesh/Decimate")
    e.hint += " Voxel reconstruction (Method) also rebuilds it, as a volume."
    return e


def validate(mesh, output=False):
    """The input must be a clean surface (if not, Repair Mesh fixes it). For the remesher's own output the same test
    says the remesh went wrong, which the caller retries at a higher density."""
    import igl
    v, f = np.asarray(mesh.vertices), np.asarray(mesh.faces, dtype=np.int64)
    problem = None
    if not len(f) or not np.isfinite(v).all() or not mesh.nondegenerate_faces().all():
        problem = 'it has empty, non-finite or degenerate faces'
    elif not igl.is_edge_manifold(f)[0] or not np.all(igl.is_vertex_manifold(f)):
        problem = 'it is not manifold'
    elif not mesh.is_winding_consistent:
        problem = 'its faces are not wound consistently'
    if problem:
        if output:
            raise UnusableOutput(f'The remeshed surface is not usable: {problem}.')
        raise _needs_repair(mesh)


def topology(mesh):
    # Native helper operations preserve manifold topology; verify Euler characteristic and boundary loops too.
    counts = np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))
    border = mesh.edges_unique[counts == 1]
    loops = 0
    if len(border):
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        ids, inv = np.unique(border, return_inverse=True)
        edges = inv.reshape(-1, 2)
        graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(ids), len(ids)))
        loops = connected_components(graph, directed=False, return_labels=False)
    return int(mesh.euler_number), int(loops)


def samples(mesh, count=6000):
    rng = np.random.default_rng(731)
    area = mesh.area_faces
    ids = rng.choice(len(area), size=min(count, max(500, len(area))), p=area / area.sum())
    uv = rng.random((len(ids), 2))
    reflected = uv.sum(1) > 1
    uv[reflected] = 1 - uv[reflected]
    tri = mesh.vertices[mesh.faces[ids]]
    pts = tri[:, 0] + uv[:, :1] * (tri[:, 1] - tri[:, 0]) + uv[:, 1:] * (tri[:, 2] - tri[:, 0])
    vertices = mesh.vertices[np.linspace(0, len(mesh.vertices)-1, min(count//2, len(mesh.vertices)), dtype=int)]
    return np.concatenate([pts, vertices])


def surface_distances(source, output):
    import igl
    distances = []
    for a, b in ((source, output), (output, source)):
        squared, _, _ = igl.point_mesh_squared_distance(samples(a), np.asarray(b.vertices, float), np.asarray(b.faces, np.int64))
        distances.append(np.sqrt(np.maximum(squared, 0)))
    d = np.concatenate(distances)
    return dict(sample_max=float(d.max()), sample_rms=float(np.sqrt(np.mean(d*d))), samples=len(d))


def boundary_error(source, output):
    """Compare the actual boundary polylines; distance to the whole skin can conceal a moved border."""
    import igl
    borders=[]
    for mesh in (source,output):
        counts=np.bincount(mesh.edges_unique_inverse,minlength=len(mesh.edges_unique))
        borders.append(mesh.edges_unique[counts==1])
    if not len(borders[0]) and not len(borders[1]):
        return 0.
    if not len(borders[0]) or not len(borders[1]):
        return float('inf')
    maximum=0.
    for a,b,edges,reference in ((source,output,borders[0],borders[1]),(output,source,borders[1],borders[0])):
        points=np.concatenate([a.vertices[np.unique(edges)],a.vertices[edges].mean(1)])
        squared,_,_=igl.point_mesh_squared_distance(points,np.asarray(b.vertices),np.asarray(reference,np.int64))
        maximum=max(maximum,float(np.sqrt(np.maximum(squared,0)).max()))
    return maximum


def dual_cells(mesh, preserve_features=True, feature_angle=45., irregularity=0., seed=1):
    """Feature-split barycentric dual. Each source triangle is tiled exactly by three corner patches."""
    v, f = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if irregularity:
        # Move face centres inside their source triangles; cells continue to tile exactly and stay on the skin.
        rng = np.random.default_rng(seed)
        bary = (1-irregularity)*np.full((len(f), 3), 1/3) + irregularity*rng.dirichlet([8, 8, 8], len(f))
        centres = np.einsum('fi,fij->fj', bary, v[f])
    else:
        centres = v[f].mean(1)
    edges = mesh.edges_unique
    mids = v[edges].mean(1)
    vertices = np.concatenate([v, mids, centres])
    mid_ids = mesh.edges_unique_inverse.reshape(-1, 3) + len(v)
    centre_ids = np.arange(len(f)) + len(v) + len(edges)
    blocked = set()
    if preserve_features:
        blocked = {tuple(e) for e in mesh.face_adjacency_edges[mesh.face_adjacency_angles >= np.radians(feature_angle)]}
    # Corner patch loops share an original vertex. Merge adjacent patches only across non-feature spokes.
    patches = defaultdict(list)
    for fi, face in enumerate(f):
        for j, vertex in enumerate(face):
            patches[int(vertex)].append((fi, [int(vertex), int(mid_ids[fi, j]), int(centre_ids[fi]), int(mid_ids[fi, (j-1)%3])]))
    polygons = []
    for vertex, entries in patches.items():
        remaining = {fi: loop for fi, loop in entries}
        # Boundary edge cancellation gives a loop, also for open fans and feature-separated wedges.
        while remaining:
            first = next(iter(remaining))
            group, queue = {}, [first]
            while queue:
                fi = queue.pop()
                if fi not in remaining:
                    continue
                loop = remaining.pop(fi)
                group[fi] = loop
                for fj in list(remaining):
                    common = set(f[fi]) & set(f[fj])
                    if len(common) == 2 and tuple(sorted(common)) not in blocked:
                        queue.append(fj)
            directed = set()
            for loop in group.values():
                for a, b in zip(loop, loop[1:]+loop[:1]):
                    if (b, a) in directed:
                        directed.remove((b, a))
                    else:
                        directed.add((a, b))
            next_vertex = dict(directed)
            start = min(next_vertex)
            loop, current = [], start
            while True:
                loop.append(current)
                current = next_vertex[current]
                if current == start:
                    break
                if len(loop) > len(directed):
                    raise ValueError('Cannot form a simple dual cell around a vertex.')
            # Interior source vertices disappear naturally when all corner patches meet.
            loop = np.array(loop, np.int64)
            try:
                triangulate(vertices, (loop,))
                polygons.append(loop)
            except ValueError:
                # Strongly folded cells may have no simple dominant-plane projection. Preserve
                # the same barycentric tiling as smaller native wedges rather than inventing a cap.
                polygons.extend(np.array(patch, np.int64) for patch in group.values())
    used, inv = np.unique(np.concatenate(polygons), return_inverse=True)
    cuts = np.cumsum([0]+[len(p) for p in polygons])
    faces = tuple(inv[cuts[i]:cuts[i+1]] for i in range(len(polygons)))
    return from_arrays(vertices[used], faces)


def blender_surface(surface, values, ctx):
    from atlas_toolkit.geom import blender as gb
    import tempfile
    with tempfile.TemporaryDirectory(prefix='atlas-remesh-blender-') as folder:
        source, output = Path(folder)/'in.npz', Path(folder)/'out.npz'
        np.savez(source, V=surface.vertices, F=surface.triangles.astype(np.int32))
        client = gb.client()
        client.request(dict(op='remesh', src=str(source), out=str(output), params=values),
                       on_progress=ctx.progress, poll=ctx.check_cancel)
        with np.load(output, allow_pickle=False) as data:
            corners, offsets = data['corners'], data['offsets']
            return from_arrays(data['V'], tuple(corners[offsets[i]:offsets[i+1]] for i in range(len(offsets)-1)))


class ResolutionError(ValueError):
    """The selected method needs more elements to meet preservation constraints. A surface-error miss carries the
    measured error and the tolerance (fractions of the diagonal) and the face count it was measured at."""
    sample_max = tolerance = faces = None


class UnusableOutput(ValueError):
    """The remesher's own result is broken (degenerate or non-manifold faces). A denser retry makes more of them."""


class SurfaceError(ValueError):
    def __init__(self, message, sample_max, tolerance):
        super().__init__(message)
        self.sample_max, self.tolerance = sample_max, tolerance


# How a remesh's largest surface error falls as its faces grow: about faces^-0.75 on scans (1.53% -> 0.17% over 16
# times the faces on a 25 000-face bust), slower than smooth theory (^-1) because of fine scan detail.
ERROR_EXPONENT = 0.75
MAX_BOOST = 16
MAX_FACES = 2_000_000       # the most a remesh may make (_process refuses more): a retry past it is never tried


def _process(surface: Surface, values, ctx, frame=None):
    import trimesh
    import igl
    p = dict(values)
    original = surface.as_trimesh()
    method = p['method']
    if method != 'voxel':
        validate(original)
    del original
    origin = surface.bounds.mean(0)
    diagonal = float(np.linalg.norm(surface.extents))
    if diagonal <= 0:
        raise ValueError('The mesh has zero extent.')
    # Normalisation protects native float32 bindings and keeps every tolerance independent of input units.
    normalized = from_arrays((surface.vertices-origin)/diagonal, surface.faces)
    mesh = normalized.as_trimesh()
    factor = .01 if p['length_units'] == 'relative' else 1/diagonal
    target = int(p['target_faces']) if p['density'] == 'faces' else max(4, round(len(surface.faces)*p['retain']/100))
    length = float(p['edge_length'])*factor
    if p['density'] == 'length':
        area_per_face = length**2 * (1 if method == 'quads' else np.sqrt(3)/4)
        target = max(4, round(mesh.area / area_per_face))
    else:
        divisor = target*(2 if method == 'cells' else 1)
        length = np.sqrt(mesh.area / (max(divisor, 4)*(1 if method == 'quads' else np.sqrt(3)/4)))
    requested = target
    boost = p.get('_density_boost', 1)
    target *= boost
    length /= np.sqrt(boost)
    if target > 2000000:
        raise ValueError('Requested density exceeds two million native faces; increase edge length.')
    error = float(p['surface_error'])/100
    if method == 'voxel':
        p['voxel_size'] *= factor
        if p['voxel_size'] <= 0 or np.prod(np.maximum(mesh.extents/p['voxel_size'], 1)) > 300000000:
            raise ValueError('Voxel grid is too dense; increase Voxel size.')
    p['_target'] = target
    p['_length'] = length
    if method in ('uniform', 'adaptive', 'directional', 'cells') and p.get('iterations'):
        # measured: about 12 s per iteration on 1.2 million faces (projection included), plus the first pass
        seconds = (target + len(surface.faces)) * (int(p['iterations']) + 4) * 6e-6
        if seconds > 60:
            ctx.warn(f"{method.capitalize()} triangles at about {f'{int(target):,}'.replace(',', ' ')} faces take about "
                     f"{max(1, round(seconds / 60))} minute{'s' if seconds >= 90 else ''}. Cancel stops it at any time; "
                     "fewer Iterations or a lower face target are faster.")
    if method == 'decimate' and target >= len(surface.faces):
        output = from_arrays(surface.vertices.copy(), surface.faces)
        return output, dict(method=method,target=requested,processing_target=target,polygons=len(output.faces),
            triangles=len(output.triangles),components=len(mesh.split(only_watertight=False,repair=False)),
            sample_max=0.,sample_rms=0.,sample_max_percent=0.,samples=0,source_diagonal=diagonal,native_peak_mb=0.)
    pieces = mesh.split(only_watertight=False, repair=False)
    if not len(pieces):
        raise ValueError('The mesh has no usable surface components.')
    if method == 'voxel' and not p['separate_shells']:
        pieces = [mesh]
    area = sum(part.area for part in pieces)
    outputs, diagnostics = [], []
    for index, part in enumerate(pieces):
        ctx.check_cancel()
        sub = SubContext(ctx, .08 + .77*index/len(pieces), .77/len(pieces))
        portion = max(4, round(target * part.area/area))
        v, f = np.asarray(part.vertices), np.asarray(part.faces)
        weights = np.zeros(len(v))
        if p['regional_detail'] and method in ('decimate', 'uniform', 'adaptive', 'directional', 'cells'):
            from atlas_toolkit.geom.influence import weights as influence
            world = v*diagonal+origin
            canonical = frame.apply(world) if frame is not None else world
            lo, hi = (frame.lo, frame.hi) if frame is not None else (surface.bounds[0], surface.bounds[1])
            weights = influence(canonical, p, lo, hi)
        try:
            if method in ('voxel', 'planar') or (method == 'quads' and p['quad_algorithm'] == 'quadriflow'):
                settings = dict(p, _target=portion)
                result = blender_surface(from_mesh(part), settings, sub)
            elif method == 'quads':
                import pynanoinstantmeshes as nim
                from flowrep3d.mesh import _native_stdout
                if len(f) > max(25000, portion*4):
                    # A checked QEM preparation avoids feeding millions of noisy, tiny triangles
                    # into the field solve. Projection and final validation still use the full source.
                    prep = dict(method='decimate',target=max(25000,portion*4),length=length,
                        minimum=length,maximum=length,error=min(error*.1,.001),normal=45.,
                        angle=p['feature_angle'] if p['preserve_features'] else 0.,
                        boundary=int(p['preserve_boundary']),iterations=1,density=1.,project=1)
                    v, f = backend.remesh(v, f, np.zeros(len(v)), prep, SubContext(sub,0,.2))
                with _native_stdout():
                    vv, ff = nim.remesh(np.ascontiguousarray(v, dtype=np.float32), np.ascontiguousarray(f,dtype=np.uint32),
                        max(4, portion//4), rosy=4, posy=4, creaseAngle=p['feature_angle'] if p['preserve_features'] else -1.,
                        align_to_boundaries=p['preserve_boundary'], smooth_iter=2, deterministic=True)
                from flowrep3d.mesh import _clean_orient
                vv, ff, repairs = _clean_orient(vv, ff)
                result = from_arrays(vv, ff)
            else:
                settings = dict(method='adaptive' if method == 'adaptive' or (method == 'cells' and p['cell_base']=='adaptive') else
                    'decimate' if method == 'decimate' else 'uniform', target=portion, length=length,
                    minimum=length*p['min_edge'], maximum=length*p['max_edge'], error=error,
                    normal=p['normal_deviation'], angle=p['feature_angle'] if p['preserve_features'] else 0.,
                    boundary=int(p['preserve_boundary']), iterations=p['iterations'],
                    density=p['regional_density'] if p['regional_detail'] else 1., project=int(p['project']))
                transform = np.eye(3)
                if method == 'directional':
                    direction = np.asarray(p['direction'], float)
                    if np.linalg.norm(direction) < 1e-9:
                        raise ValueError('Long-edge direction must be a nonzero vector.')
                    if frame is not None:
                        direction = direction @ frame.R
                    direction /= np.linalg.norm(direction)
                    transform += (1/p['elongation']-1)*np.outer(direction, direction)
                    # A metric transform changes dihedral angles. The condition-number angle
                    # bound conservatively retains every source feature above the user's threshold.
                    if p['preserve_features']:
                        settings['angle'] = np.degrees(2*np.arctan(np.tan(np.radians(settings['angle'])/2)/p['elongation']))
                    # Match element area in the altered metric rather than simply multiplying output density.
                    transformed_area = trimesh.Trimesh(v@transform.T, f, process=False).area
                    settings['length'] *= np.sqrt(transformed_area/part.area)
                vv, ff = backend.remesh(v@transform.T, f, weights, settings, sub)
                if getattr(sub, 'features_noisy', None):
                    reached, expected = sub.features_noisy
                    counts = f'{reached:,} faces where about {expected:,} were due'.replace(',', ' ')
                    ctx.warn("Keep sharp features: this model's sharp edges are mostly scan noise. They stopped the "
                             f'remesher from simplifying ({counts}), so it was remeshed without them. Raise Sharp '
                             'feature angle to keep only the strongest edges.')
                vv = vv @ np.linalg.inv(transform).T
                result = from_arrays(vv, ff)
                if method == 'cells':
                    result = dual_cells(result.as_trimesh(), p['preserve_features'], p['feature_angle'], p['irregularity'], p['seed'])
            sub.check_cancel()
            out = result.as_trimesh()
            validate(out, output=True)
            if method != 'voxel':
                if topology(part) != topology(out):
                    raise ValueError('Remeshing changed surface topology. Increase density or select another method.')
                if p['preserve_boundary']:
                    if boundary_error(part,out) > 1e-6:
                        raise ValueError('This remesher moved the protected boundary. Increase density or use uniform/adaptive triangles.')
            measurement = surface_distances(part, out)
            if method != 'voxel' and measurement['sample_max'] > error*1.05 + 1e-8:
                raise SurfaceError(f"Surface error {100*measurement['sample_max']:.3g}% exceeds {p['surface_error']:g}%. "
                                   'Increase density or Surface-error tolerance.', measurement['sample_max'], error)
        except ToolError:                    # already says what to do (too many faces, say): no retry
            raise
        except ValueError as failure:
            raised = ResolutionError(str(failure))
            raised.unusable = isinstance(failure, UnusableOutput)
            if isinstance(failure, SurfaceError):
                raised.sample_max, raised.tolerance = failure.sample_max, failure.tolerance
                raised.faces = requested
            raise raised from failure
        diagnostics.append(measurement)
        measurement['native_peak_mb'] = getattr(sub, 'native_peak_mb', 0.)
        outputs.append(result)
    vertices, faces, offset = [], [], 0
    for result in outputs:
        vertices.append(result.vertices)
        faces.extend([np.asarray(face)+offset for face in result.faces])
        offset += len(result.vertices)
    native_faces = np.asarray(faces, np.int64) if all(len(f) == 3 for f in faces) else tuple(faces)
    output = from_arrays(np.concatenate(vertices)*diagonal+origin, native_faces)
    data = dict(method=method, target=requested, processing_target=target, polygons=len(output.faces), triangles=len(output.triangles),
        components=len(outputs), sample_max=max(d['sample_max'] for d in diagnostics)*diagonal,
        sample_rms=np.sqrt(np.mean([d['sample_rms']**2 for d in diagnostics]))*diagonal,
        sample_max_percent=100*max(d['sample_max'] for d in diagnostics),
        samples=sum(d['samples'] for d in diagnostics), source_diagonal=diagonal)
    data['native_peak_mb'] = max(d['native_peak_mb'] for d in diagnostics)
    ctx.progress(.9, 'validated native topology')
    return output, data


def process(surface: Surface, values, ctx, frame=None):
    """Remesh; when the result strays further from the surface than the tolerance, try again denser. A surface-error
    miss says how much denser it must be (ERROR_EXPONENT): one retry at that density, or, when it would take more
    than MAX_BOOST times the faces, stop at once and say what would work, instead of doubling for minutes."""
    p = dict(values)
    boost = 1.0
    for attempt in range(5):
        p['_density_boost'] = boost
        try:
            output, diagnostics = _process(surface, p, ctx, frame)
            diagnostics['density_attempts'] = attempt+1
            return output, diagnostics
        except ResolutionError as error:
            if p['method'] in ('decimate','planar','voxel') or attempt == 4:
                raise ValueError(str(error)) from error
            if getattr(error, 'unusable', False):
                from atlas_toolkit.core.errors import ToolError
                raise ToolError(f"{p['method'].capitalize()} triangles left a broken surface on this model ({error})",
                                hint='Use Uniform triangles or Decimate for it. A denser retry only makes more broken '
                                     'faces, so the tool does not try one.') from error
            if error.sample_max:
                need = boost * (error.sample_max / error.tolerance) ** (1 / ERROR_EXPONENT) * 1.15
                faces = int(error.faces or 0)                           # the target asked for, before any boost
                if need > MAX_BOOST or faces * need > MAX_FACES:
                    from atlas_toolkit.core.errors import ToolError
                    at_target = error.sample_max * boost ** ERROR_EXPONENT
                    asked, needed = (f'{n:,}'.replace(',', ' ') for n in (faces, int(faces * need)))
                    raise ToolError(
                        f"{p['method'].capitalize()} triangles at about {asked} faces stay within "
                        f"{100*at_target:.2g}% of the surface, and the Surface-error tolerance asks for "
                        f"{100*error.tolerance:.2g}%.",
                        hint=(f"Raise Surface-error tolerance (Preservation) to about {100*at_target*1.1:.2g}%, or the "
                              f"face target to about {needed} faces (more than {MAX_BOOST} times this one, or more "
                              f"than {MAX_FACES // 1_000_000} million faces, which Remesh/Decimate does not try by "
                              "itself).")) from error
                boost = max(need, boost * 1.5)
                ctx.warn(f'{error} Retrying {p["method"]} at {boost:.1f} times the density, which should meet it.')
            else:
                boost *= 2
                ctx.warn(f'{error} Retrying {p["method"]} with a denser mesh to preserve the surface.')
    raise AssertionError('unreachable')
