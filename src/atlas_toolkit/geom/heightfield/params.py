"""Shared physical heightfield controls; image preparation and GIS input stay in their tools."""
from atlas_toolkit.core import Bool, Choice, Float, Int, Section, paper_params


def heightfield_params(image=False):
    sections = [
        Section('Surface',
            Choice('method', 'Method', 'faithful', [('faithful', 'Faithful terrain'), ('smooth', 'Smooth terrain'),
                ('faceted', 'Faceted terrain'), ('terraces', 'Contour terraces')]),
            Choice('sampling', 'Sampling', 'grid', [('grid', 'Regular grid'), ('adaptive', 'Adaptive TIN')],
                   visible_if={'method': ('faithful', 'smooth')}),
            Choice('preview_quality', 'Preview quality', 'balanced', [('fast', 'Fast · 25k'), ('balanced', 'Balanced · 100k'), ('detailed', 'Detailed · 250k')]),
            Float('vertical_error', 'Adaptive vertical tolerance', .02, unit='mm', min=.001, max=10., decimals=3, visible_if={'sampling': 'adaptive','method':('faithful','smooth')}),
            Float('maximum_edge', 'Adaptive maximum XY edge', 1., unit='mm', min=.05, max=20., visible_if={'sampling': 'adaptive','method':('faithful','smooth')}, help='Largest horizontal terrain-triangle edge after physical scaling.'),
            Float('boundary_error', 'Boundary tolerance', .025, unit='mm', min=0., max=1., decimals=3),
            Int('triangle_budget', 'Export triangle budget', 2000000, min=1000, max=8000000, advanced=True),
            Float('pitch', 'Sampling pitch', .25, unit='mm', min=.01, max=20., decimals=2,
                  visible_if=lambda v: v.get('sampling')!='adaptive' or v.get('method') in ('faceted','terraces'),
                  help='Approximate grid spacing before terrain rotation. The export budget defaults to two million triangles; advanced maximum eight million.'),
            Float('smooth_radius', 'Smoothing radius', 1.5, unit='mm', min=0., max=50., visible_if={'method': 'smooth'}),
            Int('target_faces', 'Terrain triangle target', 2500, min=50, max=2000000, visible_if={'method': 'faceted'}),
            Float('surface_error', 'Surface-error tolerance', .75, unit='mm', min=.001, max=50., decimals=3,
                  visible_if={'method': 'faceted'}),
            Float('terrace_step', 'Terrace step', 1., unit='mm', min=.05, max=100., visible_if={'method': 'terraces'})),
        Section('Model size', *paper_params('A4', 'portrait', 10., custom=True)),
        Section('Relief',
            Choice('height_mode', 'Height scaling', 'geographic', [('geographic', 'Geographic scale'), ('target', 'Target relief height')],
                   help='Use geographic vertical scale or set an explicit relief height.'),
            Float('relief_height', 'Target relief height', 20., unit='mm', min=.1, max=2000.,visible_if={'height_mode':'target'}),
            Float('z_factor', 'Z-factor', 1., min=.01, max=100., decimals=2),
            Float('basal_height', 'Basal lift', 2., unit='mm', min=0., max=500.,
                  help='Added above the plate, independently of the terrain relief and Z-factor.')),
        Section('Base plate',
            Choice('plate_fit', 'Plate rectangle', 'snug', [('snug', 'Snug padded fit'), ('full', 'Full available rectangle'), ('custom', 'Custom plate')]),
            Float('plate_width', 'Plate width', 190., unit='mm', min=1., max=10000., visible_if={'plate_fit': 'custom'}),
            Float('plate_height', 'Plate height', 277., unit='mm', min=1., max=10000., visible_if={'plate_fit': 'custom'}),
            Bool('plate_aspect', 'Lock plate aspect', False, visible_if={'plate_fit': 'custom'}),
            Bool('lock_scale', 'Lock terrain scale', False),
            Float('terrain_width', 'Locked raster width', 180., unit='mm', min=.01, max=10000., decimals=6, visible_if={'lock_scale': True}, help='Unrotated raster extent width, including masked areas; turning or resizing the plate preserves this physical scale.'),
            Float('padding', 'Minimum padding', 5., unit='mm', min=0., max=500.),
            Choice('base_mode', 'Thickness by', 'mm', [('mm', 'Millimetres'), ('percent', '% maximum relief')]),
            Float('base_height', 'Plate thickness', 2., unit='mm', min=.2, max=500., visible_if={'base_mode': 'mm'}),
            Float('base_percent', 'Plate / relief', 20., unit='%', min=.1, max=500., visible_if={'base_mode': 'percent'}),
            Float('base_minimum', 'Minimum plate thickness', 1., unit='mm', min=.2, max=100., visible_if={'base_mode': 'percent'}),
            Float('corner_radius', 'Corner radius', 0., unit='mm', min=0., max=500., help='Zero gives square corners.')),
        Section('Placement', Float('yaw', 'Terrain turn', 0., unit='°', min=-180., max=180., decimals=1,
                                  help='Clockwise from grid north; the plate remains aligned with the page.')),
        Section('Geometry', Choice('geometry', 'Export geometry', 'solid', [('solid', 'Printable solid'),
            ('surface', 'Terrain surface · drawing')], help='Drawing surfaces omit the plate, pedestal and outside walls; they are open.')),
    ]
    if image:
        for section in sections:
            section.title = section.title.replace('Base plate', 'Layout and base plate')
            section.params = [p for p in section.params if p.key != 'height_mode']
            for p in section.params:
                p.label = p.label.replace('Terrain', 'Relief').replace('terrain', 'image')
                p.help = p.help.replace('terrain', 'relief')
                if p.key == 'method':
                    p.choices = [('faithful', 'Faithful relief'), ('smooth', 'Smooth relief'),
                                 ('faceted', 'Faceted relief'), ('terraces', 'Contour terraces')]
                if p.key == 'geometry':
                    p.choices = [('solid', 'Printable solid'), ('surface', 'Relief surface · drawing')]
                if p.key == 'yaw':
                    p.label = 'Image rotation'
                    p.help = 'Clockwise from the top of the image; the plate stays aligned with the page.'
                if p.key == 'relief_height':
                    p.visible_if=None
                    p.label = 'White relief height'
                    p.help = 'Black maps to zero, white to this height before the Z-factor. Tones are never stretched after preparation.'
                if p.key in ('base_mode', 'base_height', 'base_percent', 'base_minimum', 'basal_height'):
                    old = p.visible_if
                    p.visible_if = lambda values, old=old: values.get('geometry') == 'solid' and (not old or all(values.get(k) == v for k,v in old.items()))
    return sections


def topology_params(default_clearance=0.):
    return Section('Mask topology',
        Choice('contact_policy','Point contacts','manual',[('manual','Manual clearance · zero rejects'),('automatic','Automatic safe clearance')],
               help='Automatic trims only diagonal contacts using the physical source-cell size; holes and islands remain.'),
        Float('contact_trim','Point-contact clearance',default_clearance,unit='mm',min=0.,max=1.,decimals=4,advanced=True,
              visible_if={'contact_policy':'manual'},help='Zero rejects source point contacts on export. Positive clearance must be below a quarter of the physical source-cell edge; Placement shows the current limit.'))
