"""Top-view terrain placement. All exported controls live in the ordinary parameter form."""
import math
import numpy as np
from PySide6.QtCore import QPointF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from atlas_toolkit.ui.widgets.param_form import DoubleSpin
from atlas_toolkit.geom.heightfield.source import footprint_polygon, outline
from atlas_toolkit.geom.heightfield.mesh import layout, best_yaw, polygons, transform_xy


class PlacementView(QWidget):
    title = 'Placement'
    read = None
    source_message = 'Add a GIS DEM.'
    reading_message = 'Reading terrain footprint…'
    reset_title = 'Reset north'
    rotation_title = 'Terrain rotation'
    lock_title = 'Lock terrain scale'
    fit_title = 'Fit terrain'
    heading_title = 'Grid N'
    instruction = 'Drag the north handle to rotate elevations on the plate. This angle is exported.'
    absolute_tones = False
    info_changed = Signal(str)
    loaded = Signal(int, object, str)
    aligned = Signal(int, float)
    SOURCE_KEYS = ()

    def __init__(self, page):
        super().__init__()
        self.page, self.theme = page, page.theme
        self.values = dict(page.values())
        self.path, self.footprint, self.data = None, None, None
        self.hull = None
        self.elevations, self.source_size = None, None
        self.geographic = False
        self.message = self.source_message
        self.generation = 0
        self.alignment_token = 0
        self.invalid = False
        self.authoritative = False
        self.contact_count=0
        self.detail_estimate=0
        self.paths = {}
        self.geometry_key = None
        self.path_key = None
        self.dialog = None
        self.root = QVBoxLayout(self)
        self.content = QWidget()
        self.root.addWidget(self.content)
        lay = QVBoxLayout(self.content)
        bar = QHBoxLayout()
        self.toolbar = bar
        north = QPushButton(self.reset_title)
        north.clicked.connect(lambda: self.commit(0.))
        self.best = QPushButton('Best fit')
        self.best.clicked.connect(self.align)
        bar.addWidget(north)
        bar.addWidget(self.best)
        bar.addStretch(1)
        expand = QPushButton('Expand…')
        expand.clicked.connect(self.expand)
        bar.addWidget(expand)
        lay.addLayout(bar)
        rotation = QHBoxLayout()
        rotation.addWidget(QLabel(self.rotation_title))
        self.angle = DoubleSpin()
        self.angle.setRange(-180., 180.)
        self.angle.setDecimals(1)
        self.angle.setSuffix('°')
        self.angle.setKeyboardTracking(False)
        self.angle.setValue(self.values['yaw'])
        self.angle.valueChanged.connect(self.commit)
        rotation.addWidget(self.angle)
        rotation.addStretch(1)
        lay.addLayout(rotation)
        sizing = QHBoxLayout()
        self.width_control, self.height_control = DoubleSpin(), DoubleSpin()
        for title,control in [('Plate W',self.width_control),('H',self.height_control)]:
            sizing.addWidget(QLabel(title))
            control.setRange(1.,10000.)
            control.setDecimals(3)
            control.setSuffix(' mm')
            control.setKeyboardTracking(False)
            sizing.addWidget(control)
        self.aspect = QCheckBox('Plate aspect')
        self.aspect.toggled.connect(lambda on:self.change({'plate_aspect':on}))
        sizing.addWidget(self.aspect)
        self.width_control.valueChanged.connect(lambda: self.resize_plate(self.width_control.value(),self.height_control.value()))
        self.height_control.valueChanged.connect(lambda: self.resize_plate(self.width_control.value(),self.height_control.value()))
        lay.addLayout(sizing)
        actions = QHBoxLayout()
        self.lock = QCheckBox(self.lock_title)
        self.lock.toggled.connect(self.lock_terrain)
        actions.addWidget(self.lock)
        fit = QPushButton(self.fit_title)
        self.fit_button = fit
        fit.clicked.connect(self.fit_terrain)
        reset = QPushButton('Reset plate')
        reset.clicked.connect(lambda: self.change(dict(plate_fit='snug',lock_scale=False)))
        actions.addWidget(fit)
        actions.addWidget(reset)
        lay.addLayout(actions)
        instruction = QLabel(self.instruction)
        instruction.setWordWrap(True)
        instruction.setMinimumWidth(0)
        lay.addWidget(instruction)
        self.topology_info=QLabel()
        self.topology_info.setWordWrap(True)
        lay.addWidget(self.topology_info)
        self.canvas = PlacementCanvas(self)
        lay.addWidget(self.canvas, 1)
        self.loaded.connect(self._loaded)
        self.aligned.connect(self._aligned)
        self.timer = QTimer(self, singleShot=True, interval=650, timeout=self._preview)

    def set_theme(self, theme):
        self.theme = theme
        self.canvas.update()

    def set_art(self, art):
        self.canvas.update()

    def fit(self):
        self.canvas.update()

    def info_text(self):
        if self.invalid:
            return f'Does not fit · use {self.fit_title} or larger paper'
        if self.data:
            return f"{self.data['plate_width']:.1f} × {self.data['plate_height']:.1f} mm · turn {self.values['yaw']:.1f}°"
        return self.message

    def set_input(self, path, info):
        path = str(path) if path else None
        if path == self.path:
            return
        self.path = path
        self.alignment_token += 1
        self.values = dict(self.page.values())
        self._sync_angle()
        self.generation += 1
        self.footprint, self.data, self.elevations, self.source_size = None, None, None, None
        self.timer.stop()
        if path:
            self._load()
        else:
            self.message = self.source_message
            self.canvas.update()

    def _load(self):
        self.generation += 1
        generation, path, values = self.generation, self.path, dict(self.values)
        self.data = None
        self.authoritative = False
        self.message = self.reading_message
        self.canvas.update()
        def work():
            try:
                from atlas_toolkit.core.job import JobContext
                ctx = JobContext(lambda *a:None,lambda:self.generation!=generation)
                for size in (1024,6144):
                    try:
                        raster = self.read(path, values,ctx,max_size=size,quick=size==1024)
                    except ValueError:
                        if size==1024:
                            continue
                        raise
                    mask = raster.valid
                    if values['footprint'] == 'rectangle' or values['missing'] == 'minimum':
                        mask = np.ones_like(mask)
                    shape = outline(raster) if values['footprint']=='valid' and values['missing']=='expose' else footprint_polygon(mask, raster.width, raster.height)
                    ctx.check_cancel()
                    stride = max(1, int(np.ceil(max(mask.shape)/1024)))
                    heights = raster.heights[::stride, ::stride]
                    valid = raster.valid[::stride, ::stride]
                    lo, hi = (0., 1.) if self.absolute_tones else raster.metadata['source_range']
                    gray = np.nan_to_num(np.clip((heights-lo)/max(hi-lo, 1e-12), 0., 1.))
                    rgba = np.empty((*gray.shape, 4), np.uint8)
                    rgba[:, :, :3] = (gray[:, :, None]*255 if self.absolute_tones else gray[:, :, None]*190+40).astype(np.uint8)
                    rgba[:, :, 3] = valid*255
                    ctx.check_cancel()
                    from atlas_toolkit.geom.heightfield.mesh import contact_vertices
                    contacts=(len(raster.metadata['_contacts_xy']) if '_contacts_xy' in raster.metadata else len(contact_vertices(mask))) if values['footprint']=='valid' and values['missing']=='expose' else 0
                    self.loaded.emit(generation, (shape, raster.geographic, rgba, (raster.width, raster.height),
                                                 (raster.metadata['source_columns'],raster.metadata['source_rows']),
                                                 raster.metadata.get('statistics_exact',True),contacts), '')
            except Exception as e:
                from atlas_toolkit.core.job import Cancelled
                if isinstance(e,Cancelled):
                    return
                try:
                    self.loaded.emit(generation, None, str(e))
                except RuntimeError:
                    pass  # The page was destroyed while its background inspection finished.
        QThreadPool.globalInstance().start(work)

    def _loaded(self, generation, footprint, error):
        if generation != self.generation:
            return
        self.alignment_token += 1
        self.footprint = footprint[0] if footprint else None
        self.authoritative = bool(footprint and (len(footprint)<6 or footprint[5]))
        self.contact_count=footprint[6] if footprint and len(footprint)>6 else 0
        self.hull = self.footprint.convex_hull if self.footprint is not None else None
        self.geographic = footprint[1] if footprint else False
        self.elevations = None
        self.source_size = footprint[3] if footprint else None
        self.source_shape = footprint[4] if footprint else None
        if footprint:
            rgba = footprint[2]
            self.elevations = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0],
                                     QImage.Format_RGBA8888).copy()
        self.message = error
        self._layout()
        if footprint is not None and self.authoritative:
            self.timer.start()

    def validation_error(self,path=None,mode='run'):
        if path is not None and str(path)!=self.path:return ''
        if not self.path:return ''
        if not self.authoritative:
            return self.message or 'Preparing the authoritative footprint; placement actions resume when ready.'
        if self.invalid:return self.message
        if self.data and self.contact_count:
            from atlas_toolkit.geom.heightfield.mesh import contact_clearance
            from atlas_toolkit.core.job import JobContext
            cell=min(self.source_size[0]/self.source_shape[0],self.source_size[1]/self.source_shape[1])*self.data['scale']
            try:contact_clearance(self.values,cell,JobContext(lambda *a:None,lambda:False,mode=mode))
            except ValueError as e:return str(e)
        return ''

    def values_changed(self, values):
        previous, self.values = self.values, dict(values)
        if previous != self.values:
            self.alignment_token += 1
        self._sync_angle()
        if not self.path or previous == self.values:
            return
        if any(previous.get(k) != values.get(k) for k in self.SOURCE_KEYS):
            self._load()
        else:
            self._layout()
        self.timer.start()

    def _layout(self):
        previous = self.data
        geometry_key = (id(self.footprint),self.values['yaw'],self.values['lock_scale'],self.values['terrain_width'])
        reuse = self.values['lock_scale'] and previous is not None and geometry_key==self.geometry_key
        shape = self.hull if reuse else self.footprint
        self.data = None
        self.invalid = False
        if self.footprint is not None:
            try:
                self.data = layout(shape, self.values,reference_width=self.source_size[0])
                self.message = ''
            except ValueError as e:
                self.message = str(e)
                self.invalid = True
                try:
                    self.data = layout(shape,self.values,validate=False,reference_width=self.source_size[0])
                except ValueError:
                    pass
        if self.data:
            if reuse:
                self.data['terrain'] = previous['terrain']
            self.detail_estimate=int(np.ceil(self.footprint.area*self.data['scale']**2/self.values['pitch']**2*2+
                                            self.footprint.length*self.data['scale']/self.values['pitch']*3))
            if self.values['geometry']=='solid':
                self.detail_estimate+=int(self.footprint.length*self.data['scale']/self.values['pitch']*6+16)
            path_key = (id(self.data['terrain']),self.values['padding'])
            if path_key!=self.path_key:
                display = self.data['terrain'].simplify(.15,preserve_topology=True)
                self.paths.update(terrain=self.make_path(display),padding=self.make_path(display.buffer(self.values['padding'])))
                self.path_key = path_key
            self.paths['plate'] = self.make_path(self.data['plate'])
            self.geometry_key = geometry_key
            for control,key in [(self.width_control,'plate_width'),(self.height_control,'plate_height')]:
                control.blockSignals(True)
                control.setValue(self.data[key])
                control.blockSignals(False)
            cell=min(self.source_size[0]/self.source_shape[0],self.source_size[1]/self.source_shape[1])*self.data['scale']
            self.topology_info.setText(f'{self.contact_count} source point contacts · allowed manual clearance < {cell/4:.7g} mm · '
                                      f'suggested {min(.002,cell/16):.7g} mm, or Automatic safe clearance'
                                      if self.authoritative else 'Preparing authoritative footprint…')
        self.info_changed.emit(self.info_text())
        self.fit_button.setEnabled(self.authoritative)
        self.lock.setEnabled(self.authoritative)
        self.width_control.setEnabled(self.authoritative)
        self.height_control.setEnabled(self.authoritative)
        if hasattr(self.page,'_update_controls'):self.page._update_controls()
        self.canvas.update()

    def commit(self, angle):
        angle = round((angle+180) % 360-180, 1)
        self.change({'yaw': angle})

    def change(self,changes):
        if self.page.busy():
            self._sync_angle()
            return
        self.page.form.set_values(changes)
        self.values_changed(self.page.values())
        self.page._settings_changed()

    def lock_terrain(self,on):
        if self.data and self.authoritative:
            self.change(dict(lock_scale=on,terrain_width=self.locked_width(self.data['scale'])))

    def locked_width(self,scale,reuse=True):
        if reuse and self.values.get('lock_scale'):
            return self.values['terrain_width']
        return np.floor(self.source_size[0]*scale*1e6+1e-7)/1e6

    def fit_terrain(self):
        if self.footprint is not None and self.authoritative:
            try:
                fit = layout(self.footprint,dict(self.values,lock_scale=False))
            except ValueError as e:
                self.message = str(e)
                self.invalid = True
                self.info_changed.emit(self.message)
                self.canvas.update()
                return
            self.change(dict(lock_scale=True,terrain_width=self.locked_width(fit['scale'],reuse=False)))

    def resize_plate(self,width,height):
        if not self.data or not self.authoritative:
            return
        old = self.data
        available = np.asarray(old['sheet'])-2*self.values['margin']
        width,height = np.minimum([width,height],available)
        width,height = max(width,1.),max(height,1.)
        if self.values['plate_aspect']:
            ratio = old['plate_width']/old['plate_height']
            if abs(width/old['plate_width']-1)>=abs(height/old['plate_height']-1):
                height = width/ratio
            else:
                width = height*ratio
            if width>available[0]:
                width,height = available[0],available[0]/ratio
            if height>available[1]:
                height,width = available[1],available[1]*ratio
        changes = dict(plate_fit='custom',lock_scale=True,terrain_width=self.locked_width(old['scale']))
        x0,y0,x1,y1 = old['terrain'].bounds
        width = max(width,x1-x0+2*self.values['padding'],2*self.values['corner_radius']+1e-7)
        height = max(height,y1-y0+2*self.values['padding'],2*self.values['corner_radius']+1e-7)
        if self.values['plate_aspect']:
            factor = max(width/old['plate_width'],height/old['plate_height'])
            width,height = old['plate_width']*factor,old['plate_height']*factor
        changes.update(plate_width=min(available[0],np.ceil(width*1000)/1000),
                       plate_height=min(available[1],np.ceil(height*1000)/1000))
        try:
            layout(self.hull,dict(self.values,**changes),reference_width=self.source_size[0])
            self.change(changes)
            return
        except ValueError:
            pass
        low,high = 0.,1.
        for _ in range(16):
            alpha = (low+high)/2
            changes.update(plate_width=old['plate_width']+(width-old['plate_width'])*alpha,
                           plate_height=old['plate_height']+(height-old['plate_height'])*alpha)
            try:
                layout(self.hull,dict(self.values,**changes),reference_width=self.source_size[0])
                low = alpha
            except ValueError:
                high = alpha
        changes.update(plate_width=old['plate_width']+(width-old['plate_width'])*low,
                       plate_height=old['plate_height']+(height-old['plate_height'])*low)
        changes['plate_width'] = min(available[0],np.ceil(changes['plate_width']*1000)/1000)
        changes['plate_height'] = min(available[1],np.ceil(changes['plate_height']*1000)/1000)
        self.change(changes)

    @staticmethod
    def make_path(geometry):
        out = QPainterPath()
        out.setFillRule(Qt.OddEvenFill)
        for poly in polygons(geometry):
            for ring in [poly.exterior,*poly.interiors]:
                coords = list(ring.coords)
                out.moveTo(*coords[0])
                for xy in coords[1:]:
                    out.lineTo(*xy)
                out.closeSubpath()
        return out

    def expand(self):
        if self.dialog:
            self.dialog.raise_()
            return
        snapshot = dict(self.page.values())
        dialog = QDialog(self.page)
        dialog.setWindowTitle(self.page.tool.name+' · placement and plate')
        dialog.setWindowFlags(dialog.windowFlags()|Qt.WindowMinMaxButtonsHint)
        dialog.resize(1100,900)
        lay = QVBoxLayout(dialog)
        self.root.removeWidget(self.content)
        lay.addWidget(self.content)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Use settings')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        self.dialog = dialog
        def finish(result):
            self.root.addWidget(self.content)
            self.dialog = None
            if result != QDialog.Accepted:
                self.change(snapshot)
            dialog.deleteLater()
        dialog.finished.connect(finish)
        dialog.open()

    def _sync_angle(self):
        self.angle.blockSignals(True)
        self.angle.setValue(self.values['yaw'])
        self.angle.blockSignals(False)
        self.lock.blockSignals(True)
        self.lock.setChecked(self.values.get('lock_scale',False))
        self.lock.blockSignals(False)
        self.aspect.blockSignals(True)
        self.aspect.setChecked(self.values.get('plate_aspect',False))
        self.aspect.blockSignals(False)

    def align(self):
        if self.footprint is None:
            return
        generation, shape, values = self.alignment_token, self.hull, dict(self.values)
        self.best.setEnabled(False)
        def work():
            try:
                from atlas_toolkit.core.job import JobContext
                angle = best_yaw(shape, values,JobContext(lambda *a:None,lambda:generation!=self.alignment_token))
                self.aligned.emit(generation, angle)
            except Exception:
                try:
                    self.aligned.emit(generation, float('nan'))
                except RuntimeError:
                    pass
        QThreadPool.globalInstance().start(work)

    def _aligned(self, generation, angle):
        self.best.setEnabled(True)
        if generation == self.alignment_token and math.isfinite(angle):
            self.commit(angle)

    def _preview(self):
        row = self.page._current_row()
        pending = row is not None and self.page.draft_job is not None and self.page.draft_job in row.jobs
        if (self.path and self.data and self.authoritative and not self.validation_error(mode='draft') and not self.canvas.dragging and not pending and not self.page.busy()
                and not self.page.ctx.lacking(self.page.tool, self.values)):
            self.page.preview_selected(keep_view=True)


class PlacementCanvas(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.dragging = False
        self.handle = QPointF()
        self.plate_handles = []
        self.resize_handle = None
        self.scale = 1.
        self.setMinimumSize(260, 260)
        self.setMouseTracking(True)
        self.setToolTip('Dashed outline: minimum padding. Drag squares to resize the plate; drag the round handle to rotate.')

    def paintEvent(self, event):
        editor, data = self.editor, self.editor.data
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), editor.theme.q('paper'))
        qp.setPen(editor.theme.q('paper-ink'))
        if not data:
            qp.drawText(self.rect().adjusted(24, 24, -24, -24), Qt.AlignCenter | Qt.TextWordWrap, editor.message)
            return
        # Use the fixed page area, so an auto-fitted plate never zooms under a moving handle.
        available = np.asarray(data['sheet'])-2*editor.values['margin']
        scale = min((self.width()-100)/available[0], (self.height()-140)/available[1])
        self.scale = scale
        centre = QPointF(self.width()/2, self.height()/2)
        def point(x, y):
            return QPointF(centre.x()+x*scale, centre.y()-y*scale)
        def path(key):
            return QTransform(scale,0.,0.,-scale,centre.x(),centre.y()).map(editor.paths[key])
        qp.setPen(QPen(editor.theme.q('paper-ink'), 1.5))
        plate_colour = editor.theme.q('paper-ink')
        plate_colour.setAlpha(30)
        qp.setBrush(plate_colour)
        qp.drawPath(path('plate'))
        padding_pen = QPen(editor.theme.q('paper-ink'), 1., Qt.DashLine)
        qp.setPen(padding_pen)
        qp.setBrush(Qt.NoBrush)
        qp.drawPath(path('padding'))
        terrain_colour = editor.theme.q('accent')
        terrain_colour.setAlpha(85)
        qp.setBrush(terrain_colour)
        qp.setPen(QPen(editor.theme.q('accent'), 1.5))
        qp.drawPath(path('terrain'))
        if editor.elevations is not None:
            w, h = editor.source_size
            image = editor.elevations
            corners = transform_xy([[-w/2, h/2, 0], [w/2, h/2, 0], [-w/2, -h/2, 0]], data, editor.values['yaw'])
            tl, tr, bl = [point(*xy) for xy in corners[:, :2]]
            qp.save()
            qp.setTransform(QTransform((tr.x()-tl.x())/image.width(), (tr.y()-tl.y())/image.width(),
                                      (bl.x()-tl.x())/image.height(), (bl.y()-tl.y())/image.height(), tl.x(), tl.y()))
            qp.drawImage(QPointF(), image)
            qp.restore()
            qp.setBrush(Qt.NoBrush)
            qp.drawPath(path('terrain'))
        self.plate_handles = []
        for x,y in [(-1,-1),(0,-1),(1,-1),(1,0),(1,1),(0,1),(-1,1),(-1,0)]:
            position = point(x*data['plate_width']/2,y*data['plate_height']/2)
            self.plate_handles.append((x,y,position))
            qp.fillRect(int(position.x()-4),int(position.y()-4),8,8,editor.theme.q('accent'))
        angle = math.radians(editor.values['yaw'])
        radius = min(available)*scale/2+24
        self.handle = QPointF(centre.x()+math.sin(angle)*radius, centre.y()-math.cos(angle)*radius)
        qp.drawLine(centre, self.handle)
        qp.setBrush(editor.theme.q('accent'))
        qp.drawEllipse(self.handle, 7, 7)
        qp.setPen(editor.theme.q('paper-ink'))
        qp.drawText(self.handle+QPointF(10, -8), editor.heading_title)
        if editor.invalid:
            qp.setPen(editor.theme.q('accent'))
            qp.drawText(self.rect().adjusted(16,self.height()-75,-16,-10),Qt.TextWordWrap,editor.message)
        else:
            qp.drawText(20, self.height()-18, editor.info_text())
        # Estimated unrotated working grid; the build reports the exact masked final count.
        estimate = editor.detail_estimate
        detail = f'Adaptive ≤ {editor.values["vertical_error"]:g} mm · budget {editor.values["triangle_budget"]:,}' if editor.values['sampling']=='adaptive' and editor.values['method'] in ('faithful','smooth') else f'Masked export ≈ {estimate:,} triangles · {editor.values["pitch"]:g} mm'
        qp.drawText(20, 24, detail)
        if editor.source_shape:
            cell = editor.source_size[0]/editor.source_shape[0]*data['scale']
            qp.drawText(20,42,f'Source cell {cell:.4g} mm · preview {editor.values["preview_quality"]}')

    def mousePressEvent(self, event):
        if self.editor.page.busy() or not self.editor.data or event.button() != Qt.LeftButton:
            return
        # The nearest handle takes the click: on a small canvas the plate's top square sits almost on the round one.
        where = event.position()
        near = []
        if self.editor.authoritative:
            near = [(distance, (x, y)) for x, y, point in self.plate_handles
                    if (distance := (where-point).manhattanLength()) < 14]
        rotate = (where-self.handle).manhattanLength()
        if rotate < 22:
            near.append((rotate, None))
        if near:
            self.dragging = True
            self.resize_handle = min(near, key=lambda n: n[0])[1]
            self.editor.timer.stop()
            self.editor.page.cancel_preview()
            self.grabMouse()

    def mouseMoveEvent(self, event):
        if self.dragging:
            d = event.position()-QPointF(self.width()/2, self.height()/2)
            if self.resize_handle:
                x,y = self.resize_handle
                self.editor.resize_plate(2*abs(d.x())/self.scale if x else self.editor.data['plate_width'],
                                         2*abs(d.y())/self.scale if y else self.editor.data['plate_height'])
            else:
                self.editor.commit(math.degrees(math.atan2(d.x(), -d.y())))

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.dragging = False
            self.resize_handle = None
            self.releaseMouse()
            self.editor.timer.start()
