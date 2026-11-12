"""GIS terrain placement using the shared physical layout editor."""
from atlas_toolkit.ui.widgets.heightfield_placement import PlacementView as BasePlacementView, PlacementCanvas
from .raster import read, SOURCE_KEYS


class PlacementView(BasePlacementView):
    read = staticmethod(read)
    SOURCE_KEYS = SOURCE_KEYS + ('footprint', 'missing')
