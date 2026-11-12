"""Small local material swatch and source credit, using Atlas widgets and theme tokens."""
import json
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget,QHBoxLayout,QLabel
from atlas_toolkit.ui.widgets.common import hint


class TexturePanel(QWidget):
    def __init__(self,page):
        super().__init__()
        self.page=page
        layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,6)
        self.swatch=QLabel(); self.swatch.setFixedSize(96,96)
        self.swatch.setAlignment(Qt.AlignCenter)
        self.credit=hint(''); self.credit.setWordWrap(True)
        self.credit.setOpenExternalLinks(True)
        layout.addWidget(self.swatch); layout.addWidget(self.credit,1)
        page.form.changed.connect(self.refresh)
        page.presets.applied.connect(self.refresh)
        QTimer.singleShot(0,self.refresh)

    def refresh(self):
        # Do not import the numerical engine on the GUI thread. Model inspection
        # can be importing trimesh/SciPy concurrently in a background thread.
        assets=Path(__file__).with_name('assets')
        values=self.page.values()
        if values.get('source')=='custom':
            self.swatch.clear(); self.swatch.setText('Heightmap')
            self.credit.setText('Custom grayscale PNG/TIFF. Black is zero height; white is full relief. The original model remains underneath.')
            return
        data=json.loads((assets/'catalog.json').read_text(encoding='utf-8'))
        entry=next(t for t in data['textures'] if t['id']==values.get('texture','scales'))
        self.swatch.setPixmap(QPixmap(str(assets/entry['thumbnail'])).scaled(96,96,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.credit.setText(f"{entry['name']} · <a href=\"{entry['source_url']}\">{entry['provider']}</a> · CC0<br>"
                            'Prepared for graphic relief. Try the A3 / 0.3 mm drawing workflows.')
