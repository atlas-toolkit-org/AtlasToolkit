"""Numeric height rasters to printable terrain and drawing surfaces."""
from atlas_toolkit.core import Bool, Choice, Float, Inputs, Int, Output, Section, Text, Tool
from atlas_toolkit.core.params import ParamError
from atlas_toolkit.geom.heightfield.params import heightfield_params,topology_params


class LegacySourceMode(Text):
    """Keep old GIS settings readable and reject heightmap steps before execution."""
    def coerce(self, value):
        value = super().coerce(value)
        if value not in ("auto", "gis"):
            raise ParamError("Non-geographic heightmaps moved to Image to Mesh. Replace this step with image-to-mesh; use --auto-levels to reproduce the old range stretch.")
        return value

TOOL = Tool(
    id='raster-dem-to-mesh', name='Raster DEM to Mesh', version='0.3.1', category='image2mesh',
    icon='raster-dem-to-mesh', action='Build mesh',
    summary='Turn a geographic numeric DEM into printable terrain or a drawing surface.',
    tagline='Geographic relief, physical dimensions and a watertight base.',
    description='Read numeric elevations, preserve masks and holes, fit the rotated terrain and padded plate '
        'to paper or custom dimensions, and build faithful, smooth, faceted or terraced relief. '
        'Printable solids are validated before export. Terrain-only surfaces are open and intended for drawing. '
        'A CRS is required in the file or override. Non-geographic images use Image to Mesh. The complete supplied extent is processed.',
    inputs=Inputs('image', exts=('.tif', '.tiff', '.png')),
    output=Output('{stem}_dem_{geometry}.stl', 'mesh', formats=('.stl', '.obj', '.ply')),
    signature='atlas-toolkit:raster-dem-to-mesh', engine='.engine:run', inspect='.engine:inspect',
    input_dependencies='.engine:input_dependencies', viewer='.viewer:PlacementView',
    runs_in='process', preview='mesh', columns=('faces', 'height', 'watertight'),
    preset_inputs=('source_mode','band','source_crs','elevation_units','metadata_scale','footprint','exclude_background',
                   'background','background_tolerance','missing','gap_size','contact_trim','contact_policy'),
    sample='.engine:make_sample', test_values={'pitch': 4., 'paper': 'A5'},
    requires_if=(('remesh', {'method': 'faceted'}),),
    optional=(('remesh', 'Boundary-preserving faceted terrain; bundled with Atlas'),),
    params=[
        Section('Input raster',
            LegacySourceMode('source_mode', 'Legacy GIS interpretation', 'gis', visible_if=lambda values: False, advanced=True, help='Legacy auto/gis settings use GIS. Non-geographic heightmaps moved to image-to-mesh.'),
            Int('band', 'Elevation band', 1, min=1, max=1024),
            Text('source_crs', 'CRS override', '', advanced=True, help='Assign a missing CRS or override an incorrect one, e.g. EPSG:3763.'),
            Choice('elevation_units', 'Elevation units', 'auto', [('auto', 'Metadata / metres'), ('m', 'Metres'),
                ('ft', 'International feet'), ('us-ft', 'US survey feet')], advanced=True),
            Bool('metadata_scale', 'Apply band scale / offset', True, advanced=True),
            Choice('footprint', 'Footprint', 'valid', [('valid', 'Valid data'), ('rectangle', 'Raster rectangle')]),
            Bool('exclude_background', 'Exclude background value', False),
            Float('background', 'Background value', 0., min=-1e12, max=1e12, visible_if={'exclude_background': True}),
            Float('background_tolerance', 'Background tolerance', 0., min=0., max=1e9,
                  visible_if={'exclude_background': True}, help='Applied to raw band values before metadata scaling.'),
            Choice('missing', 'Missing areas', 'expose', [('expose', 'Expose plate'), ('gaps', 'Fill small internal gaps'),
                ('minimum', 'Fill to terrain minimum')]),
            Float('gap_size', 'Maximum gap diameter', 2., unit='mm', min=.1, max=100., visible_if={'missing': 'gaps'})),
        topology_params(),
        *heightfield_params(),
    ],
)
