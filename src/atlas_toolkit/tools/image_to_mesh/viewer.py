"""Image placement with neutral orientation cues and shared physical plate controls."""
from PySide6.QtWidgets import QPushButton
from atlas_toolkit.ui.widgets.heightfield_placement import PlacementView as BasePlacementView
from .image_to_mesh import read, SOURCE_KEYS


class PlacementView(BasePlacementView):
    read = staticmethod(read)
    SOURCE_KEYS = SOURCE_KEYS + ('footprint', 'missing')
    source_message = 'Add a photo or grayscale image.'
    reading_message = 'Preparing image and silhouette…'
    reset_title = 'Reset rotation'
    rotation_title = 'Image rotation'
    lock_title = 'Lock image scale'
    fit_title = 'Fit image'
    heading_title = 'Image top'
    instruction = 'Drag the rotation handle to turn the image on the plate. Black is zero relief; white is the requested height.'
    absolute_tones = True

    def __init__(self, page):
        super().__init__(page)
        for label, change in [('↶ 90°', -1), ('↷ 90°', 1)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, change=change: self.change({'quarter_turns': str((int(self.values['quarter_turns'])+change) % 4)}))
            self.toolbar.insertWidget(self.toolbar.count()-2, button)
