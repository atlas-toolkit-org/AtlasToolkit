"""Heightfield construction, physical layout and validated terrain solids."""
import numpy as np
from scipy import ndimage as ndi
from shapely import affinity
from shapely.geometry import box, Polygon, MultiPolygon, MultiLineString
from .source import footprint_polygon

MAX_FACES = 8_000_000


class PhaseContext:
    def __init__(self, parent, start, span):
        self.parent, self.start, self.span = parent, start, span

    def progress(self, fraction, message=''):
        self.parent.progress(self.start+self.span*fraction, message)

    def check_cancel(self):
        self.parent.check_cancel()


def rounded_plate(width, height, radius):
    if min(width, height) <= 0 or radius < 0 or radius > min(width, height)/2 + 1e-8:
        raise ValueError('Corner radius must fit within half the plate width and height.')
    if radius == 0:
        return box(-width/2, -height/2, width/2, height/2)
    return box(-width/2+radius, -height/2+radius, width/2-radius, height/2-radius).buffer(radius, quad_segs=24)


def layout(footprint, p, validate=True, reference_width=None):
    from atlas_toolkit.geom.paper import sheet
    w, h = sheet(p['paper'], p['orientation'], (p['paper_width'], p['paper_height']))
    aw, ah = w-2*p['margin'], h-2*p['margin']
    pad, radius = p['padding'], p['corner_radius']
    if min(aw, ah) <= 2*pad or radius >= min(aw, ah)/2:
        raise ValueError('Paper size, margin, padding and corner radius leave no room for terrain.')
    rotated = affinity.rotate(footprint, -p['yaw'], origin=(0, 0))
    x0, y0, x1, y1 = rotated.bounds
    centre = ((x0+x1)/2, (y0+y1)/2)
    rotated = affinity.translate(rotated, -centre[0], -centre[1])
    bw, bh = x1-x0, y1-y0
    if min(bw, bh) <= 0:
        raise ValueError('Terrain footprint has no area.')
    if p['plate_fit'] == 'custom':
        aw, ah = p['plate_width'], p['plate_height']
        if validate and (aw > w-2*p['margin']+1e-7 or ah > h-2*p['margin']+1e-7):
            raise ValueError('Custom plate exceeds the available page. Select larger paper or reduce the plate.')
    # A convex plate contains the padded footprint iff it contains its padded hull.
    # Buffering the original coastline repeatedly can take minutes on real DEMs.
    hull = rotated.convex_hull
    full = rounded_plate(aw, ah, radius).buffer(1e-8)
    inner = full.buffer(-pad) if pad else full
    upper = min((aw-2*pad)/bw, (ah-2*pad)/bh)
    def placed(s):
        return affinity.scale(rotated, s, s, origin=(0, 0))
    lo, hi = 0., upper
    for _ in range(0 if p.get('lock_scale') else 45):
        mid = (lo+hi)/2
        if inner.covers(affinity.scale(hull, mid, mid, origin=(0, 0))):
            lo = mid
        else:
            hi = mid
    scale = lo
    if p.get('lock_scale', False):
        scale = p['terrain_width']/max(reference_width or footprint.bounds[2]-footprint.bounds[0], 1e-20)
        if validate and not inner.covers(affinity.scale(hull, scale, scale, origin=(0, 0))):
            raise ValueError(f'Locked terrain {bw*scale:.4g} × {bh*scale:.4g} mm plus {pad:g} mm padding '
                             f'exceeds the {aw:.4g} × {ah:.4g} mm plate or its rounded corners (radius {radius:g} mm). '
                             'Enlarge the plate/paper to preserve scale, or use Fit terrain to explicitly rescale.')
    if scale <= 1e-12:
        raise ValueError('The requested plate cannot contain the padded terrain.')
    terrain = placed(scale)
    pw, ph = aw, ah
    if p['plate_fit'] == 'snug':
        sw, sh = max(scale*bw+2*pad, 2*radius+1e-7), max(scale*bh+2*pad, 2*radius+1e-7)
        required = affinity.scale(hull, scale, scale, origin=(0, 0)).buffer(pad)
        low, high = 0., 1.
        for _ in range(40):
            alpha = (low+high)/2
            pw, ph = sw+(aw-sw)*alpha, sh+(ah-sh)*alpha
            if rounded_plate(pw, ph, radius).buffer(1e-8).covers(required):
                high = alpha
            else:
                low = alpha
        pw, ph = sw+(aw-sw)*high, sh+(ah-sh)*high
    return dict(scale=scale, centre=centre, plate=rounded_plate(pw, ph, radius), terrain=terrain,
                plate_width=pw, plate_height=ph, sheet=(w, h), yaw=p['yaw'])


def best_yaw(footprint, p, ctx=None):
    candidates = []
    for angle in range(-90, 90):
        if ctx:
            ctx.check_cancel()
        try:
            data = layout(footprint, dict(p, yaw=float(angle),lock_scale=False))
            candidates.append((data['scale'], -abs(angle), -angle, angle))
        except ValueError:
            continue
    if not candidates:
        raise ValueError('No orientation fits the selected plate.')
    return float(max(candidates)[-1])


def transform_xy(vertices, data, yaw):
    angle = np.radians(-yaw)
    matrix = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    v = np.asarray(vertices, float).copy()
    v[:, :2] = (v[:, :2] @ matrix.T - data['centre'])*data['scale']
    return v


def check_mesh(mesh, solid=True, single=True, budget=MAX_FACES):
    import trimesh
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError('Terrain produced an empty or non-finite mesh.')
    if len(mesh.faces) > budget:
        raise ValueError(f'Mesh exceeds the {budget:,} triangle budget. Increase pitch/error tolerance or the export budget.')
    if np.any(mesh.area_faces <= 1e-14):
        raise ValueError('Collapsed terrain triangles. Increase sampling pitch or simplify the source mask.')
    edges, count = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    if np.any(count > 2) or (solid and np.any(count != 2)):
        raise ValueError('Non-manifold or open terrain edges. Clean point contacts in the raster mask or increase sampling resolution.')
    # Graph nodes are face corners. Links join corners at the same vertex across
    # a shared edge; disconnected fans therefore remain distinct even on bow ties.
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    adjacency, shared = mesh.face_adjacency, mesh.face_adjacency_edges
    faces = np.asarray(mesh.faces)
    endpoints = shared.reshape(-1)
    pairs = np.repeat(adjacency, 2, axis=0)
    a = pairs[:, 0]*3 + np.argmax(faces[pairs[:, 0]] == endpoints[:, None], axis=1)
    b = pairs[:, 1]*3 + np.argmax(faces[pairs[:, 1]] == endpoints[:, None], axis=1)
    graph = coo_matrix((np.ones(len(a), np.uint8), (a, b)), shape=(faces.size, faces.size)).tocsr()
    _, labels = connected_components(graph, directed=False)
    fans = np.unique(np.column_stack((faces.ravel(), labels)), axis=0)
    if len(fans) != len(mesh.vertices):
        raise ValueError('Terrain has a point contact (non-manifold vertex). Clean or fill the raster mask.')
    if not mesh.is_winding_consistent:
        raise ValueError('Terrain winding is inconsistent.')
    if solid:
        if not mesh.is_watertight or mesh.volume <= 0 or (single and trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces)).max() != 0):
            raise ValueError('Printable output must be one closed, positive-volume solid.')


def contact_vertices(valid):
    a, b, c, d = valid[:-1, :-1], valid[:-1, 1:], valid[1:, :-1], valid[1:, 1:]
    return np.argwhere((a & d & ~b & ~c) | (b & c & ~a & ~d))+1


def contact_clearance(p,cell_mm,ctx,contacts=True):
    limit=cell_mm/4
    recommended=min(.002,cell_mm/16)
    requested=recommended if p.get('contact_policy')=='automatic' else p['contact_trim']
    if ctx.draft and requested<=0:requested=recommended
    if contacts and (requested<=0 or requested>=limit):
        reason='Source mask has diagonal point contacts.' if requested<=0 else 'Point-contact clearance must be smaller than a quarter of the physical source-cell edge.'
        raise ValueError(f'{reason} Allowed clearance: 0 < value < {limit:.7g} mm; '
                         f'suggested {recommended:.7g} mm, or select Automatic safe clearance. Changing mesh pitch does not repair source contacts.')
    return requested


def trimmed_footprint(valid, width, height, trim,scale=1.):
    from shapely.ops import unary_union
    result = footprint_polygon(valid, width, height)
    contacts = contact_vertices(valid)
    if not len(contacts):
        return result
    if trim <= 0:
        cell_mm=min(width/valid.shape[1],height/valid.shape[0])*scale
        raise ValueError(f'Source mask has diagonal point contacts. Set positive clearance below {cell_mm/4:.7g} mm '
                         f'(suggested {min(.002,cell_mm/16):.7g} mm), or select Automatic safe clearance.')
    rows, cols = valid.shape
    if trim >= min(width/cols, height/rows)/4:
        raise ValueError(f'Point-contact clearance must be smaller than a quarter of the source-cell edge: {min(width/cols,height/rows)*scale/4:.7g} mm.')
    diamonds = []
    for row, col in contacts:
        x, y = col/cols*width-width/2, height/2-row/rows*height
        diamonds.append(Polygon([(x-trim,y),(x,y+trim),(x+trim,y),(x,y-trim)]))
    return result.difference(unary_union(diamonds))


def source_boundary(raster,valid,p,scale,ctx):
    from .source import outline
    from shapely.ops import unary_union
    source = '_outline_wkb' in raster.metadata and p['footprint']=='valid' and p['missing']=='expose'
    cells=raster.metadata.get('source_cell_size',[raster.width/raster.metadata.get('source_columns',valid.shape[1]),
                                                raster.height/raster.metadata.get('source_rows',valid.shape[0])])
    contacts=raster.metadata['_contacts_xy'] if source else contact_vertices(valid)
    clearance=contact_clearance(p,min(cells)*scale,ctx,contacts=len(contacts)>0)
    if source:
        boundary = outline(raster)
        if contacts:
            trim = clearance/scale
            boundary = boundary.difference(unary_union([Polygon([(x-trim,y),(x,y+trim),(x+trim,y),(x,y-trim)]) for x,y in contacts]))
    else:
        boundary = trimmed_footprint(valid,raster.width,raster.height,clearance/scale,scale)
    if len(contacts):ctx.log(f'mask topology: separated {len(contacts):,} point contacts at {clearance:.7g} mm; source-cell limit {min(cells)*scale/4:.7g} mm')
    if p['boundary_error']:
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds
        candidate = boundary.simplify(p['boundary_error']/scale,preserve_topology=True)
        transform = from_bounds(-raster.width/2,-raster.height/2,raster.width/2,raster.height/2,valid.shape[1],valid.shape[0])
        covered = rasterize([(candidate,1)],out_shape=valid.shape,transform=transform,dtype='uint8')
        reference = valid
        if source:
            reference = valid & (rasterize([(boundary,1)],out_shape=valid.shape,transform=transform,dtype='uint8')!=0)
        if np.all(covered[reference]):
            boundary = candidate
        else:
            ctx.warn('Retaining the exact boundary because simplification would remove valid source samples.')
    return boundary


def top_grid(heights, valid, width, height, footprint=None):
    import trimesh
    rows, cols = valid.shape
    x, y = np.meshgrid(np.linspace(-width/2, width/2, cols+1), np.linspace(height/2, -height/2, rows+1))
    vertices = np.column_stack((x.ravel(), y.ravel(), heights.ravel()))
    grid = np.arange((rows+1)*(cols+1)).reshape(rows+1, cols+1)
    affected = np.zeros_like(valid)
    for row, col in contact_vertices(valid):
        affected[row-1:row+1, col-1:col+1] = True
    affected &= valid
    a = grid[:-1, :-1][valid & ~affected]
    faces = np.vstack((np.column_stack((a, a+cols+1, a+1)), np.column_stack((a+1, a+cols+1, a+cols+2))))
    if affected.any():
        import mapbox_earcut
        extra_v, extra_f, cached = [], [], {}
        for row, col in np.argwhere(affected):
            cell = box(x[row,col], y[row+1,col], x[row,col+1], y[row,col])
            for poly in polygons(cell.intersection(footprint)):
                poly = poly.simplify(min(width/cols,height/rows)*1e-9, preserve_topology=True)
                xy = np.asarray(poly.exterior.coords)[:-1]
                indices = []
                for px, py in xy:
                    gx, gy = (px+width/2)/width*cols, (height/2-py)/height*rows
                    if abs(gx-round(gx)) < 1e-8 and abs(gy-round(gy)) < 1e-8:
                        indices.append(int(grid[round(gy),round(gx)]))
                        continue
                    key = (round(gx,10),round(gy,10))
                    if key not in cached:
                        u, v = gx-col, gy-row
                        z = (1-v)*((1-u)*heights[row,col]+u*heights[row,col+1])+v*((1-u)*heights[row+1,col]+u*heights[row+1,col+1])
                        cached[key] = len(vertices)+len(extra_v)
                        extra_v.append([px,py,z])
                    indices.append(cached[key])
                keep = np.array([i for i in range(len(indices)) if indices[i] != indices[i-1]])
                xy = xy[keep]
                indices = np.asarray(indices)[keep]
                if len(indices) < 3:
                    continue
                triangles = mapbox_earcut.triangulate_float64(np.ascontiguousarray(xy),np.array([len(xy)],np.uint32)).reshape(-1,3)
                tri = xy[triangles]
                a, b = tri[:,1]-tri[:,0], tri[:,2]-tri[:,0]
                signed = np.sum(a[:,0]*b[:,1]-a[:,1]*b[:,0])
                if signed < 0:
                    triangles = triangles[:,::-1]
                extra_f.extend(indices[triangles].tolist())
        vertices = np.vstack((vertices,extra_v))
        faces = np.vstack((faces,extra_f))
    mesh = trimesh.Trimesh(vertices, faces, process=False)
    mesh.remove_unreferenced_vertices()
    return mesh


def close_skin(top, bottom):
    import trimesh
    v, f = np.asarray(top.vertices), np.asarray(top.faces)
    edges = top.edges_sorted
    unique, inverse, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    boundary = top.edges[counts[inverse] == 1]
    n = len(v)
    vertices = np.vstack((v, np.column_stack((v[:, :2], np.full(n, bottom)))))
    side = np.vstack((np.column_stack((boundary[:, 1], boundary[:, 0], boundary[:, 0]+n)),
                      np.column_stack((boundary[:, 1], boundary[:, 0]+n, boundary[:, 1]+n))))
    return trimesh.Trimesh(vertices, np.vstack((f, f[:, ::-1]+n, side)), process=False)


def polygons(geometry):
    if isinstance(geometry, Polygon):
        return [geometry] if geometry.area > 1e-12 else []
    return [p for g in getattr(geometry, 'geoms', ()) for p in polygons(g)]


def extruded(poly, bottom, top):
    import trimesh
    mesh = trimesh.creation.extrude_polygon(poly, top-bottom, engine='earcut')
    mesh.apply_translation([0, 0, bottom])
    return mesh


def solid_union(parts):
    """Keep boolean coordinates in double precision, including very narrow terrace intersections."""
    import manifold3d as md
    import trimesh
    solids = [md.Manifold(md.Mesh64(np.asarray(m.vertices, np.float64), np.asarray(m.faces, np.uint64))) for m in parts]
    if any(m.status() != md.Error.NoError for m in solids):
        raise ValueError('A terrain component is not manifold. Clean point contacts in the source mask.')
    result = md.Manifold.batch_boolean(solids, md.OpType.Add).simplify(1e-8)
    if result.status() != md.Error.NoError:
        raise ValueError('Terrain and plate could not be fused into a manifold solid.')
    mesh = result.to_mesh64()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=False)


def terraces(centre_heights, mask, width, height, data, p, base, ctx):
    import trimesh
    from skimage.measure import find_contours
    from shapely import build_area
    step = p['terrace_step']
    maximum = float(centre_heights[mask].max())
    levels = int(np.floor(maximum/step+1e-9))
    if levels > 500:
        raise ValueError('More than 500 terraces requested. Increase terrace step.')
    footprint = data['terrain']
    bottom = -max(step, .1) if p['geometry'] == 'surface' and base == 0 else base*.5
    pedestal_top = base+p['basal_height']
    # A small initial slab is covered by the plate if basal lift is zero.
    parts = [extruded(poly, bottom, max(pedestal_top, base)) for poly in polygons(footprint)]
    surfaces = []
    rows, cols = mask.shape
    field = np.where(mask, centre_heights, -step)
    padded = np.pad(field, 1, constant_values=-step)
    regions = []
    for level in range(1, levels+1):
        ctx.progress(.3+.35*level/max(levels, 1), f'building terrace {level}/{levels}')
        lines = []
        # Include a plateau exactly on the threshold; marching squares otherwise
        # drops its highest tread when no sample is strictly above that level.
        for contour in find_contours(padded, (level-1e-9)*step, fully_connected='low'):
            xy = np.column_stack(((contour[:, 1]-.5)/cols*width-width/2,
                                  height/2-(contour[:, 0]-.5)/rows*height))
            v = transform_xy(np.column_stack((xy, np.zeros(len(xy)))), data, p['yaw'])
            if len(v) >= 4:
                lines.append(v[:, :2])
        if not lines:
            continue
        region = build_area(MultiLineString(lines)).intersection(footprint)
        for poly in polygons(region):
            parts.append(extruded(poly, bottom, pedestal_top+level*step))
        regions.append(region)
    if p['geometry'] == 'surface':
        # Union first, then retain terrain-facing treads and risers while dropping external walls and bottoms.
        combined = solid_union(parts)
        centres = combined.triangles_center
        normals = combined.face_normals
        from shapely import points, distance
        boundary_distance = distance(footprint.boundary, points(centres[:, :2]))
        keep = (normals[:, 2] > .5) | ((np.abs(normals[:, 2]) < .5) & (boundary_distance > 1e-5))
        surface = combined.submesh([np.flatnonzero(keep)], append=True)
        return surface
    return parts


def process(raster, p, ctx):
    import trimesh
    from .source import footprint_polygon
    unit_height = raster.metadata.get('height_reference') == 'unit'
    if unit_height and p['geometry'] == 'surface':
        p = dict(p, base_mode='mm', base_height=0., basal_height=0.)
    h, valid = raster.heights.copy(), raster.valid.copy()
    if p['missing'] == 'minimum':
        h[~valid] = h[valid].min()
        valid[:] = True
    elif p['footprint'] == 'rectangle':
        # Rectangular footprints still need finite heights at every cell.
        indices = ndi.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        h[~valid] = h[tuple(indices[:, ~valid])]
        valid[:] = True
    ctx.progress(.06,'analysing valid-data footprint')
    from .source import outline
    footprint = outline(raster) if p['footprint']=='valid' and p['missing']=='expose' else footprint_polygon(valid, raster.width, raster.height)
    ctx.progress(.075,'fitting terrain and plate')
    data = layout(footprint, p,reference_width=raster.width)
    budget = p.get('triangle_budget',2000000)
    if p['missing'] == 'gaps':
        holes = ndi.binary_fill_holes(valid) & ~valid
        labels, n = ndi.label(holes)
        distances = ndi.distance_transform_edt(~valid, sampling=(raster.height/valid.shape[0], raster.width/valid.shape[1]))*data['scale']
        for label,bounds in enumerate(ndi.find_objects(labels),1):
            ctx.check_cancel()
            region = labels[bounds] == label
            if 2*distances[bounds][region].max() <= p['gap_size']:
                bounds = tuple(slice(max(0,s.start-1),min(valid.shape[i],s.stop+1)) for i,s in enumerate(bounds))
                region = labels[bounds] == label
                from scipy.interpolate import griddata
                boundary = ndi.binary_dilation(region) & raster.valid[bounds]
                points = np.column_stack(np.nonzero(boundary))
                targets = np.column_stack(np.nonzero(region))
                interpolated = griddata(points, h[bounds][boundary], targets, method='linear')
                if not np.isfinite(interpolated).all():
                    raise ValueError('An internal gap lacks enough surrounding elevations for interpolation.')
                h[bounds][region] = interpolated
                valid[bounds][region] = True
        footprint = footprint_polygon(valid, raster.width, raster.height)
        data = layout(footprint, p,reference_width=raster.width)
    scale = data['scale']
    if p.get('sampling') == 'adaptive' and p['method'] in ('faithful','smooth') and not ctx.draft:
        return adaptive_process(raster,h,valid,footprint,data,p,ctx)
    boundary = source_boundary(raster,valid,p,scale,ctx)
    data['terrain'] = affinity.scale(affinity.translate(affinity.rotate(boundary,-p['yaw'],origin=(0,0)),
                                                     -data['centre'][0],-data['centre'][1]),scale,scale,origin=(0,0))
    preview_budget = {'fast':25000,'balanced':100000,'detailed':250000}[p.get('preview_quality','balanced')]
    pitch = max(p['pitch'],np.sqrt(raster.width*raster.height*scale**2*2/preview_budget)) if ctx.draft else p['pitch']
    cols, rows = max(2, int(np.ceil(raster.width*scale/pitch))), max(2, int(np.ceil(raster.height*scale/pitch)))
    from .resources import preflight
    estimated_ram = preflight(max(valid.size,(rows+1)*(cols+1)),min(rows*cols*2,budget))
    ctx.progress(.1, f'sampling {cols} × {rows} terrain cells')
    source_rows, source_cols = valid.shape
    nearest = ndi.distance_transform_edt(~valid, return_distances=False, return_indices=True) if not valid.all() else None
    filled = h[tuple(nearest)] if nearest is not None else h
    if p['method'] == 'smooth' and p['smooth_radius'] > 0:
        sigma = (p['smooth_radius']/(raster.height/source_rows*scale), p['smooth_radius']/(raster.width/source_cols*scale))
        weights = ndi.gaussian_filter(valid.astype(float), sigma)
        smoothed = ndi.gaussian_filter(np.where(valid, h, 0.), sigma)/np.maximum(weights, 1e-30)
        filled = np.clip(smoothed, h[valid].min(), h[valid].max())
        if nearest is not None:
            filled = filled[tuple(nearest)]
    order = 3 if p['method'] == 'smooth' else 1
    processed_range = [float(filled[valid].min()),float(filled[valid].max())] if p['method']=='smooth' and p['smooth_radius']>0 else raster.metadata.get('source_range',[float(h[valid].min()),float(h[valid].max())])
    if unit_height:
        processed_range = [0., 1.]
    def sample(array, ys, xs, order=order):
        yy, xx = np.meshgrid(ys, xs, indexing='ij')
        return ndi.map_coordinates(array, [yy, xx], order=order, mode='nearest')
    centres_y = (np.arange(rows)+.5)*source_rows/rows-.5
    centres_x = (np.arange(cols)+.5)*source_cols/cols-.5
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    if ctx.draft:
        # Draft geometry uses its own coarse mask. Clipping a dense overview
        # boundary into a light display grid adds slivers and defeats its budget.
        mask = sample(valid.astype(np.uint8),centres_y,centres_x,order=0)!=0
        draft_clearance=contact_clearance(p,min(raster.width/cols,raster.height/rows)*scale,ctx,contacts=bool(len(contact_vertices(mask))))
        boundary = trimmed_footprint(mask,raster.width,raster.height,draft_clearance/scale,scale)
        data['terrain'] = affinity.scale(affinity.translate(affinity.rotate(boundary,-p['yaw'],origin=(0,0)),
                                                         -data['centre'][0],-data['centre'][1]),scale,scale,origin=(0,0))
    else:
        mask = rasterize([(boundary,1)],out_shape=(rows,cols),transform=from_bounds(-raster.width/2,-raster.height/2,raster.width/2,raster.height/2,cols,rows),all_touched=True,dtype='uint8')!=0
    if not mask.any():
        raise ValueError('Sampling lost every valid cell. Decrease sampling pitch.')
    estimate=int(mask.sum())*2
    ctx.progress(.11,f'estimated masked terrain {estimate:,} triangles; complete-output budget {budget:,}')
    if boundary.equals(box(-raster.width/2,-raster.height/2,raster.width/2,raster.height/2)) and estimate>budget:
        suggested=pitch*np.sqrt(estimate/budget)*1.03
        raise ValueError(f'Rectangular terrain needs {estimate:,} triangles before plate/walls; budget {budget:,}. '
                         f'Use grid pitch about {suggested:.3g} mm or larger, or raise the export budget.')
    heights = sample(filled, np.linspace(-.5, source_rows-.5, rows+1), np.linspace(-.5, source_cols-.5, cols+1))
    centre_heights = sample(filled, centres_y, centres_x)
    heights = np.clip(heights, *processed_range)
    centre_heights = np.clip(centre_heights, *processed_range)
    minimum, maximum = processed_range
    span = maximum-minimum
    zscale = scale if raster.geographic and p.get('height_mode', 'target') == 'geographic' else p['relief_height']/span if span else 0.
    zscale *= p['z_factor']
    heights = (heights-minimum)*zscale
    centre_heights = (centre_heights-minimum)*zscale
    relief = (float(filled[valid].max()) if unit_height else span)*zscale
    base = p['base_height'] if p['base_mode'] == 'mm' else max(p['base_minimum'], relief*p['base_percent']/100)
    if p['method'] == 'terraces':
        built = terraces(centre_heights, mask, raster.width, raster.height, data, p, base, ctx)
        if p['geometry'] == 'surface':
            output = built
        else:
            plate = extruded(data['plate'], 0., base)
            ctx.progress(.7, 'fusing terrain terraces and plate')
            output = solid_union([plate, *built])
    else:
        # Conservative grid coverage followed by polygon clipping retains tiny
        # islands and holes even when no regular sample centre lands inside them.
        top = top_grid(heights+base+p['basal_height'], mask, raster.width, raster.height,boundary)
        rectangular = box(-raster.width/2,-raster.height/2,raster.width/2,raster.height/2)
        if not ctx.draft and not boundary.equals(rectangular):
            from .adaptive import clip,export_precision
            top.vertices[:,:2] *= scale
            top = clip(top,affinity.scale(boundary,scale,scale,origin=(0,0)),budget,ctx)
            top = export_precision(top,ctx)
            top.vertices[:,:2] /= scale
        top.vertices = transform_xy(top.vertices, data, p['yaw'])
        check_mesh(top, solid=False)
        if p['method'] == 'faceted' and len(top.faces) > p['target_faces']:
            from atlas_toolkit.geom.remesh_backend import remesh
            origin = top.bounds.mean(axis=0)
            diagonal = float(np.linalg.norm(top.extents))
            settings = dict(method='decimate', target=p['target_faces'], length=.01, minimum=.01, maximum=.1,
                            error=p['surface_error']/diagonal, normal=60., angle=0., boundary=1,
                            iterations=1, density=1., project=1)
            ctx.progress(.3, 'simplifying terrain while protecting mask boundaries')
            original = top
            for attempt in range(5):
                settings['target'] = min(len(original.faces), p['target_faces']*2**attempt)
                settings['normal'] = 30./2**attempt
                if settings['target'] >= len(original.faces) or attempt == 4:
                    top = original
                    break
                v, f = remesh((original.vertices-origin)/diagonal, original.faces,
                              np.zeros(len(original.vertices)), settings, PhaseContext(ctx,.3+.07*attempt,.07))
                candidate = trimesh.Trimesh(v*diagonal+origin, f, process=False)
                xy = candidate.vertices[candidate.faces, :2]
                a, b = xy[:,1]-xy[:,0], xy[:,2]-xy[:,0]
                areas = a[:,0]*b[:,1]-a[:,1]*b[:,0]
                if np.all(areas > 1e-12):
                    top = candidate
                    break
                ctx.warn('Faceting would fold the XY projection; retaining more triangles to preserve the heightfield.')
            check_mesh(top, solid=False)
        if p['geometry'] == 'surface':
            output = top
        else:
            from .solid import stitch
            ctx.progress(.72, 'stitching terrain, deck and plate')
            output = stitch(top,data['plate'],base,ctx,budget=budget)
            if output is None:
                terrain = close_skin(top, base*.5)
                plate = extruded(data['plate'], 0., base)
                output = solid_union([terrain,plate])
    if p['geometry']=='solid':
        from .solid import export_precision
        output,_ = export_precision(output,ctx,allow_planar_simplify=p['method']=='terraces')
    output.remove_unreferenced_vertices()
    ctx.progress(.82, 'validating manifold topology and dimensions')
    check_mesh(output, solid=p['geometry'] == 'solid',budget=budget)
    data.update(base_height=base, basal_height=p['basal_height'], relief_height=relief, grid=(cols, rows),
                pitch=pitch, height_mode='geographic' if raster.geographic and p.get('height_mode', 'target') == 'geographic' else 'target',
                source=raster.metadata, terrain_faces=len(output.faces), geographic=raster.geographic)
    data['estimated_peak_bytes'] = estimated_ram
    return output, data


def adaptive_process(raster,h,valid,footprint,data,p,ctx):
    from .adaptive import build
    from .solid import stitch
    scale = data['scale']
    from .resources import preflight
    estimated_ram = preflight(valid.size,min(valid.size*2,p['triangle_budget']))
    if p['method']=='smooth' and p['smooth_radius']>0:
        sigma = (p['smooth_radius']/(raster.height/len(h)*scale),p['smooth_radius']/(raster.width/h.shape[1]*scale))
        weights = ndi.gaussian_filter(valid.astype(float),sigma)
        h = ndi.gaussian_filter(np.where(valid,h,0.),sigma)/np.maximum(weights,1e-30)
        h = np.clip(h,*raster.metadata.get('source_range',[h[valid].min(),h[valid].max()]))
    lo,hi = [float(h[valid].min()),float(h[valid].max())] if p['method']=='smooth' and p['smooth_radius']>0 else raster.metadata.get('source_range',[float(h[valid].min()),float(h[valid].max())])
    if raster.metadata.get('height_reference') == 'unit':
        lo, hi = 0., 1.
    span = hi-lo
    zscale = scale if raster.geographic and p.get('height_mode', 'target')=='geographic' else p['relief_height']/span if span else 0.
    relief = (float(h[valid].max()) if raster.metadata.get('height_reference') == 'unit' else span)*zscale*p['z_factor']
    base = p['base_height'] if p['base_mode']=='mm' else max(p['base_minimum'],relief*p['base_percent']/100)
    heights = (h-lo)*zscale*p['z_factor']+base+p['basal_height']
    boundary = source_boundary(raster,valid,p,scale,ctx)
    top,errors = build(heights,valid,raster.width*scale,raster.height*scale,
                       affinity.scale(boundary,scale,scale,origin=(0,0)),p,ctx)
    top.vertices[:,:2] /= scale
    top.vertices = transform_xy(top.vertices,data,p['yaw'])
    check_mesh(top,solid=False,budget=p['triangle_budget'])
    output = top
    if p['geometry']=='solid':
        ctx.progress(.72,'stitching adaptive terrain and plate')
        output = stitch(top,data['plate'],base,ctx,budget=p['triangle_budget'])
        if output is None:
            output = solid_union([close_skin(top,base*.5),extruded(data['plate'],0.,base)])
    if p['geometry']=='solid':
        from .solid import export_precision
        output,changed = export_precision(output,ctx)
        if changed:
            # Precision cleanup can touch terrain as well as deck triangles.
            # Re-measure the actual final surface against every valid source sample.
            from .adaptive import errors as measure_errors
            measured=output.copy()
            measured.update_faces(measured.face_normals[:,2]>1e-10)
            angle=np.radians(-p['yaw'])
            rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
            measured.vertices[:,:2]=(measured.vertices[:,:2]+np.asarray(data['centre'])*scale)@rotation
            maximum,rms=measure_errors(measured,heights,valid,raster.width*scale,raster.height*scale,ctx)
            errors=dict(maximum_error_mm=maximum,rms_error_mm=float(rms))
            if errors['maximum_error_mm']>p['vertical_error']+1e-5:
                raise ValueError('Final mesh-file precision exceeds adaptive vertical tolerance. Increase the tolerance or physical model size.')
    ctx.progress(.82,'validating adaptive topology')
    check_mesh(output,solid=p['geometry']=='solid',budget=p['triangle_budget'])
    data.update(errors,base_height=base,basal_height=p['basal_height'],relief_height=relief,
                grid=(h.shape[1],h.shape[0]),pitch=0.,height_mode='geographic' if raster.geographic and p.get('height_mode', 'target')=='geographic' else 'target',
                source=raster.metadata,terrain_faces=len(top.faces),geographic=raster.geographic)
    data['estimated_peak_bytes'] = estimated_ram
    return output,data
