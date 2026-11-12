"""Remesh/Decimate: signed polygon meshes for the Atlas drawing tools."""
from atlas_toolkit.core import (Bool, Choice, Float, Inputs, Int, Output, Section, Seed, Tool, Vec3,
                               frame_params, influence_params)

TRI = ('uniform', 'adaptive', 'directional', 'cells')
def method(*names):
    return lambda v: v.get('method') in names

regional = influence_params()
for param in regional:
    previous = param.visible_if
    param.visible_if = lambda v, old=previous: v.get('regional_detail', False) and v.get('method') in ('decimate', *TRI) and (
        old(v) if callable(old) else all(v.get(k) in x if isinstance(x, (tuple, list, set)) else v.get(k) == x
                                       for k, x in (old or {}).items()))


TOOL = Tool(
    id='remesh-decimate', name='Remesh/Decimate', version='0.1.1', category='mesh', icon='remesh-decimate',
    action='Apply', summary='Simplify scans or rebuild them as triangles, flowing quads, directional meshes and dual cells.',
    tagline='Mesh preparation and expressive topology for pen-plotter drawings.',
    description='Preserve detail with constrained decimation, uniform or curvature-adaptive triangles; explore '
        'aligned quads, directional triangles and dual cells. Face targets are approximate: preservation wins. '
        'OBJ and PLY retain polygons. STL triangulates them and loses their native edge pattern. '
        'Voxel reconstruction can close holes, merge shells and remove thin detail. '
        'Use the resulting mesh with Plotter Vision, Contour Lines, Hatch Maker or FlowRep.',
    inputs=Inputs('mesh'),
    output=Output('{stem}_remesh.obj', 'mesh', formats=('.obj', '.ply', '.stl'), format_notes={
        '.obj':'OBJ preserves native quads and cell boundaries.', '.ply':'PLY preserves native quads and cell boundaries.',
        '.stl':'STL triangulates polygons and loses their native edge pattern. Use OBJ or PLY for grid/cell drawings.'}),
    signature='atlas-toolkit:remesh-decimate', engine='.engine:run', inspect='.engine:inspect', runs_in='process', preview='mesh',
    columns=('polygons', 'reduction'), requires=('remesh',),
    requires_if=(('blender', {'method': ('voxel', 'planar')}),
                 ('blender', {'method': 'quads', 'quad_algorithm': 'quadriflow'})),
    optional=(('blender', 'Voxel reconstruction, planar dissolve and QuadriFlow quads'),),
    params=[
        Section('Method', Choice('method', 'Method', 'decimate', [
            ('decimate', 'Decimate'), ('uniform', 'Uniform triangles'), ('adaptive', 'Adaptive triangles'),
            ('quads', 'Aligned quads'), ('directional', 'Directional triangles'), ('cells', 'Dual cells'),
            ('planar', 'Planar dissolve · Blender'), ('voxel', 'Voxel reconstruction · Blender')]),
            Choice('quad_algorithm', 'Quad algorithm', 'instant', [('instant', 'Instant Meshes'),
                ('quadriflow', 'QuadriFlow · Blender')], visible_if=method('quads'))),
        Section('Density',
            Choice('density', 'Specify density by', 'faces', [('faces', 'Face target'), ('ratio', 'Retained %'),
                ('length', 'Edge length')], visible_if=method('decimate', *TRI, 'quads'),
                help='Counts refer to native faces, including quads or dual cells, rather than derived triangles.'),
            Int('target_faces', 'Native faces', 20000, min=50, max=2000000,
                visible_if=lambda v: v.get('method') not in ('voxel', 'planar') and v.get('density') == 'faces'),
            Float('retain', 'Retained faces', 25., unit='%', min=0.1, max=100.,
                visible_if=lambda v: v.get('method') not in ('voxel', 'planar') and v.get('density') == 'ratio'),
            Float('edge_length', 'Edge length', 1., min=0.00001, max=1000000., decimals=5,
                visible_if=lambda v: v.get('method') not in ('voxel', 'planar') and v.get('density') == 'length'),
            Choice('length_units', 'Length units', 'relative', [('relative', '% diagonal'), ('model', 'Model units')],
                help='Applies to edge length and voxel size. Surface-error tolerance is always % of the diagonal.',
                visible_if=lambda v: (v.get('density') == 'length' and v.get('method') != 'planar') or v.get('method') == 'voxel')),
        Section('Preservation',
            Bool('preserve_boundary', 'Keep open boundaries fixed', True, visible_if=lambda v: v.get('method') != 'voxel'),
            Bool('preserve_features', 'Keep sharp features', True, visible_if=lambda v: v.get('method') not in ('voxel', 'planar')),
            Float('feature_angle', 'Sharp feature angle', 45., unit='°', min=1., max=180.,
                visible_if=lambda v: v.get('preserve_features') and v.get('method') not in ('voxel', 'planar')),
            Float('surface_error', 'Surface-error tolerance', 0.1, unit='% diag.', min=0.001, max=20., decimals=3,
                visible_if=lambda v: v.get('method') != 'voxel',
                help='Constrains decimation and validates remeshing with deterministic surface samples. This is not a certified Hausdorff bound.'),
            Float('normal_deviation', 'Normal deviation limit', 60., unit='°', min=0., max=180.,
                visible_if=method('decimate'), advanced=True, help='Zero disables this additional decimation constraint.')),
        Section('Remeshing',
            Int('iterations', 'Iterations', 10, min=1, max=30, visible_if=method(*TRI)),
            Bool('project', 'Project onto source', True, visible_if=method(*TRI)),
            Float('min_edge', 'Minimum edge factor', 0.35, min=0.05, max=1., visible_if=lambda v:
                v.get('method') == 'adaptive' or (v.get('method') == 'cells' and v.get('cell_base') == 'adaptive')),
            Float('max_edge', 'Maximum edge factor', 2., min=1., max=5., visible_if=lambda v:
                v.get('method') == 'adaptive' or (v.get('method') == 'cells' and v.get('cell_base') == 'adaptive')),
            Vec3('direction', 'Long-edge direction', (0., 0., 1.), min=-1., max=1., decimals=3,
                 visible_if=method('directional')),
            Float('elongation', 'Elongation', 4., min=1., max=10., visible_if=method('directional')),
            Choice('cell_base', 'Cell distribution', 'uniform', [('uniform', 'Uniform'), ('adaptive', 'Adaptive')],
                   visible_if=method('cells')),
            Float('irregularity', 'Cell irregularity', 0., min=0., max=1., step=0.05, visible_if=method('cells')),
            Seed('seed', 'Seed', 1, visible_if=lambda v: v.get('method') == 'cells' or
                 (v.get('method') == 'quads' and v.get('quad_algorithm') == 'quadriflow')),
            Float('dissolve_angle', 'Dissolve angle', 0.5, unit='°', min=0., max=30., visible_if=method('planar'))),
        Section('Regional detail',
            Bool('regional_detail', 'Protect or emphasize a region', False, visible_if=method('decimate', *TRI),
                 help='Decimate keeps the region’s vertices. Triangle remeshing increases its density without cutting a separate patch.'),
            Float('regional_density', 'Regional density multiplier', 4., min=1., max=16.,
                  visible_if=lambda v: v.get('regional_detail') and v.get('method') in TRI), *regional),
        Section('Voxel reconstruction',
            Float('voxel_size', 'Voxel size', 1., min=0.001, max=100000., decimals=4, visible_if=method('voxel'),
                  help='Rebuilds the volume. Openings may close; nearby surfaces may merge and thin detail may disappear.'),
            Float('adaptivity', 'Adaptivity', 0., min=0., max=1., visible_if=method('voxel')),
            Bool('separate_shells', 'Process shells independently', True, visible_if=method('voxel'),
                 help='Preserves separate pieces. Turn off to reconstruct their combined volume and allow merging.')),
        Section('Model frame', *frame_params(stage_sets=False), collapsed=True),
    ], test_values={'target_faces': 120, 'surface_error': 2., 'normal_deviation': 0.},
)
