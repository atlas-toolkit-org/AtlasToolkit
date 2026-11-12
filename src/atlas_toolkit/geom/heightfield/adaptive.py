"""Error-controlled heightfield triangulation, conforming refinement and exact mask clipping."""
import numpy as np


def conform(mesh, ctx, tolerance=2e-8):
    """Split T-junctions without dropping collinear boundary vertices."""
    import shapely
    import trimesh
    _,first,inverse = np.unique(np.round(mesh.vertices,9),axis=0,return_index=True,return_inverse=True)
    mesh = trimesh.Trimesh(mesh.vertices[first],inverse[mesh.faces],process=False)
    mesh.update_faces(mesh.area_faces>1e-12)
    v, f = mesh.vertices.copy(), mesh.faces.copy()
    edges = mesh.edges_unique
    segments = v[edges,:2]
    tree = shapely.STRtree(shapely.linestrings(segments))
    splits = {}
    for start in range(0,len(v),16384):
        ctx.check_cancel()
        points, ids = tree.query(shapely.points(v[start:start+16384,:2]),predicate='dwithin',distance=tolerance)
        points = points+start
        delta = segments[ids,1]-segments[ids,0]
        t = np.sum((v[points,:2]-segments[ids,0])*delta,axis=1)/np.maximum(np.sum(delta*delta,axis=1),1e-30)
        keep = (t>1e-8)&(t<1-1e-8)
        for edge,vertex,position in zip(ids[keep],points[keep],t[keep]):
            splits.setdefault(int(edge),{})[int(vertex)] = float(position)
    if not splits:
        mesh.remove_unreferenced_vertices()
        return mesh
    edge_ids = mesh.edges_unique_inverse.reshape(-1,3)
    affected = np.any(np.isin(edge_ids,list(splits)),axis=1)
    output = [f[~affected]]
    extra = []
    for number in np.flatnonzero(affected):
        ring = []
        for j,edge in enumerate(edge_ids[number]):
            a,b = f[number,j], f[number,(j+1)%3]
            ring.append(int(a))
            if edge in splits:
                ordered = sorted(splits[edge],key=splits[edge].get,reverse=a!=edges[edge,0])
                ring.extend(ordered)
        centre = len(v)+len(extra)
        extra.append(v[f[number]].mean(axis=0))
        output.append(np.column_stack((ring,np.roll(ring,-1),np.full(len(ring),centre))))
    result = trimesh.Trimesh(np.vstack((v,extra)),np.vstack(output),process=False)
    result.remove_unreferenced_vertices()
    return result


def refine(mesh, maximum, budget, ctx):
    import trimesh
    v, f = mesh.vertices.copy(), mesh.faces.copy()
    for iteration in range(30):
        ctx.check_cancel()
        edges = np.sort(f[:, [[0,1],[1,2],[2,0]]], axis=2)
        unique, inverse = np.unique(edges.reshape(-1,2), axis=0, return_inverse=True)
        long = np.linalg.norm(v[unique[:,0],:2]-v[unique[:,1],:2], axis=1) > maximum*(1+1e-10)
        if not long.any():
            return trimesh.Trimesh(v,f,process=False)
        midpoint = np.full(len(unique), -1, np.int64)
        midpoint[long] = np.arange(long.sum())+len(v)
        v = np.vstack((v, v[unique[long]].mean(axis=1)))
        mids = midpoint[inverse].reshape(-1,3)
        requested=int(len(f)+np.count_nonzero(mids>=0))
        if requested>budget:
            raise ValueError(f'Adaptive edge refinement needs at least {requested:,} terrain triangles; budget {budget:,}. '
                             'Increase maximum XY edge, adaptive vertical tolerance, or the export budget.')
        patterns = (mids>=0).astype(np.uint8) @ np.array([1,2,4],np.uint8)
        result = []
        for pattern in range(8):
            selected = patterns == pattern
            a,b,c = f[selected].T
            x,y,z = mids[selected].T
            templates = {
                0: [(a,b,c)], 1: [(a,x,c),(x,b,c)], 2: [(b,y,a),(y,c,a)],
                4: [(c,z,b),(z,a,b)], 3: [(x,b,y),(a,x,c),(x,y,c)],
                6: [(y,c,z),(b,y,a),(y,z,a)], 5: [(z,a,x),(c,z,b),(z,x,b)],
                7: [(a,x,z),(x,b,y),(z,y,c),(x,y,z)]}
            result.extend(np.column_stack(t) for t in templates[pattern])
        f = np.vstack(result)
        if len(f)>budget:
            raise ValueError('Maximum edge refinement exceeds the triangle budget. Increase maximum edge or the export budget.')
    raise ValueError('Adaptive edge refinement did not converge.')


def clip(mesh, footprint, budget, ctx, advice='Increase grid pitch or the export budget; boundary tolerance affects only the outline.'):
    import shapely
    import mapbox_earcut
    import trimesh
    triangles = mesh.vertices[mesh.faces]
    shapes = shapely.polygons(triangles[:,:,:2])
    shapely.prepare(footprint)
    inside = shapely.covers(footprint, shapes)
    total=int(inside.sum())
    if total>budget:
        raise ValueError(f'Masked terrain already needs {total:,} interior triangles; budget {budget:,}. '+advice)
    crossing = ~inside & shapely.intersects(footprint, shapes)
    vertices, faces = [mesh.vertices], [mesh.faces[inside]]
    count = len(mesh.vertices)
    for number, index in enumerate(np.flatnonzero(crossing)):
        if number%128 == 0:
            ctx.check_cancel()
        intersection = shapes[index].intersection(footprint)
        from .mesh import polygons
        tri = triangles[index]
        matrix = np.column_stack((tri[1,:2]-tri[0,:2], tri[2,:2]-tri[0,:2]))
        for poly in polygons(intersection):
            rings = [np.asarray(r.coords)[:-1] for r in [poly.exterior,*poly.interiors]]
            xy = np.ascontiguousarray(np.vstack(rings))
            indices = mapbox_earcut.triangulate_float64(xy,np.cumsum([len(r) for r in rings],dtype=np.uint32)).reshape(-1,3)
            total+=len(indices)
            if total>budget:
                raise ValueError(f'Mask clipping needs more than {total:,} terrain triangles; budget {budget:,}. '+advice)
            uv = np.linalg.solve(matrix,(xy-tri[0,:2]).T).T
            heights = tri[0,2]+uv @ (tri[1:,2]-tri[0,2])
            vertices.append(np.column_stack((xy,heights)))
            a,b = xy[indices[:,1]]-xy[indices[:,0]],xy[indices[:,2]]-xy[indices[:,0]]
            areas = a[:,0]*b[:,1]-a[:,1]*b[:,0]
            indices[areas<0] = indices[areas<0,::-1]
            faces.append(indices+count)
            count += len(xy)
    v, f = np.vstack(vertices), np.vstack(faces)
    # Adjacent clipped triangles share their exact XY boundary, including hole edges.
    _, first, inverse = np.unique(np.round(v[:,:2],9),axis=0,return_index=True,return_inverse=True)
    output = trimesh.Trimesh(v[first],inverse[f],process=False)
    output = conform(output,ctx)
    if len(output.faces)>budget:
        raise ValueError(f'Mask clipping needs {len(output.faces):,} terrain triangles; budget {budget:,}. '+advice)
    return output


def export_precision(mesh, ctx):
    from .precision import surface
    return surface(mesh,ctx)


def errors(mesh, reference, valid, width, height, ctx):
    """Rasterize actual triangle IDs, then evaluate their planes at every valid source centre."""
    import rasterio.features
    from rasterio.transform import from_bounds
    from shapely import polygons
    triangles = mesh.vertices[mesh.faces]
    ids = rasterio.features.rasterize(((poly,i+1) for i,poly in enumerate(polygons(triangles[:,:,:2]))),
        out_shape=valid.shape, transform=from_bounds(-width/2,-height/2,width/2,height/2,valid.shape[1],valid.shape[0]),dtype='int32')
    maximum, squared, total = 0., 0., 0
    tree = None
    for row in range(0,len(valid),256):
        ctx.check_cancel()
        yy,xx = np.nonzero(valid[row:row+256])
        yy = yy+row
        selected = ids[yy,xx]-1
        xy = np.column_stack(((xx+.5)/valid.shape[1]*width-width/2,height/2-(yy+.5)/valid.shape[0]*height))
        if np.any(selected<0):
            # A source centre exactly on a coastline may move a few float32 ULPs
            # outside its clipped polygon. Still measure its height; never omit it.
            import shapely
            if tree is None:
                tree = shapely.STRtree(polygons(triangles[:,:,:2]))
            missing = np.flatnonzero(selected<0)
            point_ids, triangle_ids = tree.query(shapely.points(xy[missing]),predicate='dwithin',distance=max(width,height)*2**-20)
            if len(point_ids):
                unique, first = np.unique(point_ids,return_index=True)
                selected[missing[unique]] = triangle_ids[first]
            if np.any(selected<0):
                raise ValueError('Adaptive clipping lost valid source samples beyond format precision. Reduce boundary tolerance.')
        tri = triangles[selected]
        matrix = np.stack((tri[:,1,:2]-tri[:,0,:2],tri[:,2,:2]-tri[:,0,:2]),axis=2)
        uv = np.linalg.solve(matrix,(xy-tri[:,0,:2])[...,None])[...,0]
        z = tri[:,0,2]+np.sum(uv*(tri[:,1:,2]-tri[:,0,2,None]),axis=1)
        delta = z-reference[yy,xx]
        if len(delta):
            maximum = max(maximum,float(np.abs(delta).max()))
            squared += float(delta@delta)
            total += len(delta)
    return maximum, np.sqrt(squared/max(total,1))


def build(heights, valid, width, height, footprint, p, ctx):
    from pydelatin import Delatin
    from scipy import ndimage as ndi
    import trimesh
    budget = p['triangle_budget']
    indices = ndi.distance_transform_edt(~valid,return_distances=False,return_indices=True) if not valid.all() else None
    extended = heights[tuple(indices)] if indices is not None else heights
    field = np.pad(extended,1,mode='edge')
    ctx.progress(.25,'adaptive triangulation')
    tin = Delatin(field,max_error=p['vertical_error']*.9,max_triangles=budget)
    v = np.asarray(tin.vertices,dtype=float)
    v[:,0] = (v[:,0]-.5)/valid.shape[1]*width-width/2
    # pydelatin already reports bottom-left XY coordinates (input rows are top-down).
    v[:,1] = (v[:,1]-.5)/valid.shape[0]*height-height/2
    f = np.asarray(tin.triangles)
    mesh = trimesh.Trimesh(v,f,process=False)
    # Clip first: refining the discarded area can exceed a final-output budget
    # even when the actual masked terrain fits comfortably within it.
    ctx.progress(.4,'clipping islands and holes')
    mesh = clip(mesh,footprint,budget,ctx,'Increase adaptive vertical tolerance, maximum XY edge, boundary tolerance, or the export budget.')
    ctx.progress(.5,'conforming maximum-edge refinement')
    mesh = refine(mesh,p['maximum_edge'],budget,ctx)
    mesh = export_precision(mesh,ctx)
    mesh = refine(mesh,p['maximum_edge'],budget,ctx)
    ctx.progress(.6,'measuring source-sample vertical error')
    maximum,rms = errors(mesh,heights,valid,width,height,ctx)
    if maximum > p['vertical_error']+1e-5:
        raise ValueError(f'Adaptive maximum error {maximum:.5g} mm exceeds {p["vertical_error"]:g} mm. Increase the triangle budget or reduce boundary tolerance.')
    return mesh, dict(maximum_error_mm=maximum,rms_error_mm=float(rms))
