"""Shape Crop's Shape tab: the shared shape editor (ui/widgets/shape_editor.py), live on the GPU."""
from atlas_toolkit.ui.widgets.shape_editor import ShapeEditor


class CropView(ShapeEditor):
    preview_text = "Preview crop"
    preview_note = "Preview computes the exact result"
