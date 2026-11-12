"""Stitch a heightfield to a plate without a full-volume terrain boolean."""
import numpy as np


def export_precision(mesh,ctx,allow_planar_simplify=False):
    """Keep stitched deck slivers from collapsing when a file is reloaded.

    The surface is already manifold in double precision. Only invoke native
    topology-preserving edge collapse when a float32/merged copy loses it.
    """
    import trimesh
    from atlas_toolkit.geom.mesh import clean
    rounded=clean(trimesh.Trimesh(mesh.vertices.astype('float32'),mesh.faces,process=False))
    from .mesh import check_mesh
    try:
        check_mesh(rounded)
        try:
            check_mesh(mesh)
            return mesh,False
        except ValueError:
            # Return the validated copy, never the unsafe original it repaired.
            return rounded,True
    except ValueError:
        pass
    ctx.check_cancel()
    ctx.progress(.78,'repairing sub-format solid edges without global simplification')
    precision=max(1e-7,float(mesh.extents.max())*2**-20)
    from .precision import collapse_short_edges
    result,collapsed=collapse_short_edges(mesh,precision,ctx,heightfield=False)
    from .precision import file_copy
    try:
        check_mesh(result)
        check_mesh(file_copy(result))
    except ValueError:
        if not allow_planar_simplify:raise
        # Terraces are already a boolean-built closed volume. Keep the entire
        # solid: never infer an open heightfield by selecting its face normals.
        import manifold3d as md
        ctx.progress(.79,'stabilizing closed planar terraces')
        solid=md.Manifold(md.Mesh64(np.array(mesh.vertices,dtype=np.float64,copy=True,order='C'),
                                   np.array(mesh.faces,dtype=np.uint64,copy=True,order='C')))
        if solid.status()!=md.Error.NoError:raise ValueError('Terrace boolean returned invalid closed geometry.')
        fixed=solid.simplify(precision).to_mesh64()
        result=trimesh.Trimesh(np.asarray(fixed.vert_properties)[:,:3],np.asarray(fixed.tri_verts),process=False)
        check_mesh(result)
        check_mesh(file_copy(result))
    if collapsed:ctx.log(f'precision repair: collapsed {collapsed:,} sub-format solid edges; preserved local winding and manifold links')
    return result,True


def stitch(top, plate, base, ctx,budget=8000000):
    import trimesh
    from shapely import MultiLineString, build_area
    from .adaptive import conform
    edges = top.edges[top.edges_unique_counts[top.edges_unique_inverse] == 1] if hasattr(top,'edges_unique_counts') else top.edges[np.bincount(top.edges_unique_inverse)[top.edges_unique_inverse] == 1]
    footprint = build_area(MultiLineString(top.vertices[edges,:2].tolist()))
    # Contact cases use the existing robust manifold boolean path.
    if not plate.buffer(-1e-7).covers(footprint):
        return None
    deck = plate.difference(footprint)
    from .mesh import polygons
    outlines=polygons(footprint)
    islands=len(outlines)
    holes=sum(len(poly.interiors) for poly in outlines)
    nplate=len(plate.exterior.coords)-1
    minimum=len(top.faces)+3*len(edges)+4*nplate+2*islands-2*holes-4
    if top.bounds[0,2]>base+1e-10 and minimum>budget:
        raise ValueError(f'Complete printable solid needs at least {minimum:,} triangles including walls/deck/bottom; '
                         f'budget {budget:,}. Increase grid pitch or adaptive tolerance/maximum edge, or raise the export budget.')
    ctx.check_cancel()
    pieces = []
    for poly in polygons(deck):
        xy,faces = trimesh.creation.triangulate_polygon(poly,engine='earcut')
        vertices = np.column_stack((xy,np.full(len(xy),base)))
        boundary = np.column_stack((top.vertices[np.unique(edges),:2],np.full(len(np.unique(edges)),base)))
        mesh = trimesh.Trimesh(np.vstack((vertices,boundary)),faces,process=False)
        pieces.append(conform(mesh,ctx))
    v = top.vertices
    lower = np.column_stack((v[:,:2],np.full(len(v),base)))
    n = len(v)
    walls = np.vstack((np.column_stack((edges[:,1],edges[:,0],edges[:,0]+n)),
                       np.column_stack((edges[:,1],edges[:,0]+n,edges[:,1]+n))))
    walls_mesh = trimesh.Trimesh(np.vstack((v,lower)),walls,process=False)
    # Drop genuinely zero-height wall triangles, retaining the shared top/deck edge.
    walls_mesh.update_faces(walls_mesh.area_faces>1e-14)
    pieces.extend((top,walls_mesh))
    xy, cap = trimesh.creation.triangulate_polygon(plate,engine='earcut')
    bottom = trimesh.Trimesh(np.column_stack((xy,np.zeros(len(xy)))),cap[:,::-1],process=False)
    pieces.append(bottom)
    outline = np.asarray(plate.exterior.coords)[:-1]
    a = np.arange(len(outline)); b = np.roll(a,-1)
    # Plate polygon rings may be clockwise; derive outward wall winding from signed area.
    side = np.vstack((np.column_stack((a,b,b+len(a))),np.column_stack((a,b+len(a),a+len(a)))))
    if not plate.exterior.is_ccw:
        side = side[:,::-1]
    pieces.append(trimesh.Trimesh(np.vstack((np.column_stack((outline,np.zeros(len(a)))),
                                           np.column_stack((outline,np.full(len(a),base))))),side,process=False))
    result = trimesh.util.concatenate(pieces)
    result.merge_vertices(digits_vertex=8)
    result.remove_unreferenced_vertices()
    if not result.is_watertight or not result.is_winding_consistent:
        return None
    return result
