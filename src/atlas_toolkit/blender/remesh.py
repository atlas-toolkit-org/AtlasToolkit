"""Blender reconstruction/dissolve/quadrangulation; additive polygon transport."""
import numpy as np


def run(msg, progress):
    import bpy
    import bmesh
    import mesh_io
    v, f = mesh_io.load_npz(msg['src'])
    p = msg['params']
    obj = mesh_io.build_object('atlas_remesh', v, f)
    try:
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        progress(.1, 'Blender: '+p['method'])
        if p['method'] == 'voxel':
            modifier = obj.modifiers.new('Atlas volume reconstruction', 'REMESH')
            modifier.mode = 'VOXEL'
            modifier.voxel_size = p['voxel_size']
            modifier.adaptivity = p['adaptivity']
            modifier.use_remove_disconnected = False
        elif p['method'] == 'planar':
            bm = bmesh.new()
            try:
                bm.from_mesh(obj.data)
                bmesh.ops.dissolve_limit(bm, angle_limit=np.radians(p['dissolve_angle']),
                    use_dissolve_boundaries=not p['preserve_boundary'], verts=list(bm.verts), edges=list(bm.edges),
                    delimit={'SHARP'})
                bm.to_mesh(obj.data)
            finally:
                bm.free()
        else:
            status = bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=p['_target'],
                use_mesh_symmetry=False, use_preserve_sharp=p['preserve_features'],
                use_preserve_boundary=p['preserve_boundary'], smooth_normals=False, seed=p['seed'])
            if 'FINISHED' not in status:
                raise ValueError('QuadriFlow could not remesh this surface. Try more faces or Instant Meshes.')
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        try:
            vertices = np.empty(len(me.vertices)*3, dtype=np.float64)
            me.vertices.foreach_get('co', vertices)
            corners = np.empty(len(me.loops), dtype=np.int32)
            me.loops.foreach_get('vertex_index', corners)
            starts = np.empty(len(me.polygons), dtype=np.int32)
            me.polygons.foreach_get('loop_start', starts)
            np.savez(msg['out'], V=vertices.reshape(-1,3), corners=corners, offsets=np.r_[starts,len(corners)])
        finally:
            ev.to_mesh_clear()
        progress(1., 'Blender topology complete')
        return {'out':msg['out']}
    finally:
        mesh_io.remove(obj)
