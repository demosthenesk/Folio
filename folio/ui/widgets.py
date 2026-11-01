"""Reusable widgets: multi-page document view with overlays/tools, editable areas, thumbnails, language pickers."""
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import APPS, __version__
from ..core import ocr
from ..core.settings import settings
from .qtutil import app_icon

PAGE_GAP = 14

AREA_COLORS = {
    "text": QColor(46, 155, 74), "table": QColor(58, 100, 200), "picture": QColor(198, 60, 60),
    "background": QColor(138, 76, 192), "barcode": QColor(200, 138, 0), "recognition": QColor(90, 90, 90),
}


class MultiPageView(QGraphicsView):
    """Scrollable stack of pages rendered lazily at the current zoom.

    Page geometry is expressed in 'units' (PDF points for the PDF editor, pixels for images)."""
    pageChanged = Signal(int)
    rectDrawn = Signal(int, QRectF)
    clicked = Signal(int, QPointF)
    doubleClicked = Signal(int, QPointF)
    inkDrawn = Signal(int, list)
    lineDrawn = Signal(int, QPointF, QPointF)
    contextRequested = Signal(int, QPointF, object)
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(0x80, 0x86, 0x8c))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.sizes = []
        self.render_fn = None
        self.zoom = 1.0
        self.layout_mode = "continuous"  # continuous | two | single
        self.items = []
        self.rendered = {}
        self.overlays = {}
        self.overlays_raw = {}
        self.tool = "select"
        self.cur_page = 0
        self._drag = None
        self._band = None
        self._ink = None
        self._ink_pts = []
        self.unit_scale = 1.0  # pixels per unit at zoom 1
        self._render_timer = QTimer(self, singleShot=True, interval=30, timeout=self._render_visible)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(lambda _: self._render_timer.start())

    # -- document
    def set_document(self, sizes, render_fn, unit_scale=1.0, keep_position=False):
        pos = self.verticalScrollBar().value() if keep_position else 0
        self.sizes = list(sizes)
        self.render_fn = render_fn
        self.unit_scale = unit_scale
        self.rendered.clear()
        self._relayout()
        if keep_position:
            self.verticalScrollBar().setValue(pos)
        else:
            self.cur_page = 0
        self._render_timer.start()

    def clear(self):
        self.set_document([], None)

    def invalidate(self, pages=None):
        if pages is None:
            self.rendered.clear()
        else:
            for p in pages:
                self.rendered.pop(p, None)
        self._render_timer.start()

    def _relayout(self):
        sc = self.scene()
        for it in self.items:
            sc.removeItem(it)
        for lst in self.overlays.values():
            for _, it in lst:
                if it.scene():
                    sc.removeItem(it)
        self.overlays_raw = getattr(self, "overlays_raw", {})
        self.items = []
        s = self.zoom * self.unit_scale
        y = PAGE_GAP
        maxw = 0
        if self.layout_mode == "two":
            for i in range(0, len(self.sizes), 2):
                x = PAGE_GAP
                rowh = 0
                for j in (i, i + 1):
                    if j >= len(self.sizes):
                        break
                    w, h = self.sizes[j]
                    it = self._page_item(j, x, y, w * s, h * s)
                    x += w * s + PAGE_GAP
                    rowh = max(rowh, h * s)
                maxw = max(maxw, x)
                y += rowh + PAGE_GAP
        else:
            for i, (w, h) in enumerate(self.sizes):
                maxw = max(maxw, w * s + 2 * PAGE_GAP)
            for i, (w, h) in enumerate(self.sizes):
                x = (maxw - w * s) / 2
                self._page_item(i, x, y, w * s, h * s)
                y += h * s + PAGE_GAP
        sc.setSceneRect(0, 0, max(maxw, 10), max(y, 10))
        self.rendered.clear()
        # re-add overlays at new positions
        for tag, lst in list(self.overlays_raw.items()):
            self._place_overlays(tag)

    def _page_item(self, i, x, y, w, h):
        it = QGraphicsRectItem(0, 0, w, h)
        it.setPos(x, y)
        it.setBrush(Qt.white)
        it.setPen(QPen(QColor(60, 60, 60), 0))
        it.setZValue(0)
        pm = QGraphicsPixmapItem(it)
        pm.setTransformationMode(Qt.SmoothTransformation)
        it.pix = pm
        self.scene().addItem(it)
        self.items.append(it)
        return it

    def _visible_pages(self):
        r = self.mapToScene(self.viewport().rect()).boundingRect()
        return [i for i, it in enumerate(self.items) if it.sceneBoundingRect().intersects(r)]

    def _render_visible(self):
        if not self.render_fn:
            return
        dpr = self.devicePixelRatioF()
        vis = self._visible_pages()
        for i in vis:
            key = (self.zoom, dpr)
            if self.rendered.get(i) == key:
                continue
            w, h = self.sizes[i]
            s = self.zoom * self.unit_scale
            try:
                img = self.render_fn(i, s * dpr)
            except Exception:
                continue
            if img is None:
                continue
            pm = QPixmap.fromImage(img) if isinstance(img, QImage) else img
            pm.setDevicePixelRatio(dpr)
            it = self.items[i]
            it.pix.setPixmap(pm)
            sw = (w * s) / (pm.width() / dpr) if pm.width() else 1
            it.pix.setScale(sw)
            self.rendered[i] = key
        # free far-away pages
        for i in list(self.rendered):
            if i not in vis and abs(i - (vis[0] if vis else 0)) > 6:
                self.items[i].pix.setPixmap(QPixmap())
                self.rendered.pop(i, None)

    def _scrolled(self, _):
        self._render_timer.start()
        vis = self._visible_pages()
        if vis:
            c = self.mapToScene(self.viewport().rect().center())
            best = min(vis, key=lambda i: abs(self.items[i].sceneBoundingRect().center().y() - c.y()))
            if best != self.cur_page:
                self.cur_page = best
                self.pageChanged.emit(best)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render_timer.start()

    # -- coordinates
    def page_rect(self, i):
        return self.items[i].sceneBoundingRect()

    def to_scene(self, i, pt: QPointF):
        s = self.zoom * self.unit_scale
        return self.items[i].pos() + QPointF(pt.x() * s, pt.y() * s)

    def to_page(self, scene_pt):
        for i, it in enumerate(self.items):
            r = it.sceneBoundingRect()
            if r.adjusted(-PAGE_GAP / 2, -PAGE_GAP / 2, PAGE_GAP / 2, PAGE_GAP / 2).contains(scene_pt):
                s = self.zoom * self.unit_scale
                p = scene_pt - it.pos()
                return i, QPointF(max(0, p.x() / s), max(0, p.y() / s))
        return -1, QPointF()

    def unit_rect_to_scene(self, i, r: QRectF):
        s = self.zoom * self.unit_scale
        p = self.items[i].pos()
        return QRectF(p.x() + r.x() * s, p.y() + r.y() * s, r.width() * s, r.height() * s)

    # -- zoom
    def set_zoom(self, z, anchor_center=True):
        z = max(0.05, min(16.0, z))
        if abs(z - self.zoom) < 1e-4:
            return
        page = self.cur_page
        c = self.mapToScene(self.viewport().rect().center())
        i, up = self.to_page(c)
        self.zoom = z
        self._relayout()
        if i >= 0:
            self.centerOn(self.to_scene(i, up))
        self.zoomChanged.emit(z)
        self._render_timer.start()

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.25)

    def fit_width(self):
        if not self.sizes:
            return
        w = max(s[0] for s in self.sizes) * (2 if self.layout_mode == "two" else 1)
        avail = self.viewport().width() - 3 * PAGE_GAP
        self.set_zoom(avail / (w * self.unit_scale))

    def fit_page(self):
        if not self.sizes:
            return
        w, h = self.sizes[max(0, min(self.cur_page, len(self.sizes) - 1))]
        if self.layout_mode == "two":
            w *= 2
        z = min((self.viewport().width() - 3 * PAGE_GAP) / (w * self.unit_scale),
                (self.viewport().height() - 2 * PAGE_GAP) / (h * self.unit_scale))
        self.set_zoom(z)
        self.go_to_page(self.cur_page)

    def actual_size(self):
        # 1 unit == 1/72 inch for PDFs; assume 96 dpi screen
        self.set_zoom(96.0 / 72.0 / self.unit_scale if self.unit_scale == 1.0 else 1.0)

    def set_layout(self, mode):
        self.layout_mode = mode
        self._relayout()
        self.go_to_page(self.cur_page)

    def go_to_page(self, i):
        if 0 <= i < len(self.items):
            r = self.items[i].sceneBoundingRect()
            self.verticalScrollBar().setValue(int(r.top() - PAGE_GAP))
            self.cur_page = i
            self.pageChanged.emit(i)

    def ensure_visible(self, i, unit_rect: QRectF):
        if 0 <= i < len(self.items):
            self.ensureVisible(self.unit_rect_to_scene(i, unit_rect), 60, 60)

    # -- overlays (highlights, search results, differences...)
    def set_overlays(self, tag, rects, pen=None, brush=None, z=5):
        """rects: list of (page, QRectF in units)."""
        self.overlays_raw = getattr(self, "overlays_raw", {})
        self.overlays_raw[tag] = (list(rects), pen, brush, z)
        self._place_overlays(tag)

    def clear_overlays(self, tag):
        self.overlays_raw = getattr(self, "overlays_raw", {})
        self.overlays_raw.pop(tag, None)
        for _, it in self.overlays.pop(tag, []):
            if it.scene():
                self.scene().removeItem(it)

    def _place_overlays(self, tag):
        for _, it in self.overlays.pop(tag, []):
            if it.scene():
                self.scene().removeItem(it)
        rects, pen, brush, z = self.overlays_raw[tag]
        lst = []
        for page, r in rects:
            if page >= len(self.items):
                continue
            it = QGraphicsRectItem(self.unit_rect_to_scene(page, r))
            it.setPen(pen or QPen(Qt.NoPen))
            it.setBrush(brush or QBrush(QColor(255, 220, 0, 90)))
            it.setZValue(z)
            it.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().addItem(it)
            lst.append((page, it))
        self.overlays[tag] = lst

    # -- tools / mouse
    def set_tool(self, tool):
        self.tool = tool
        if tool == "hand":
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.viewport().setCursor(Qt.ArrowCursor if tool == "select" else Qt.CrossCursor)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15))
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        if self.tool == "hand" or e.button() != Qt.LeftButton:
            if e.button() == Qt.RightButton:
                sp = self.mapToScene(e.position().toPoint())
                i, up = self.to_page(sp)
                self.contextRequested.emit(i, up, e.globalPosition().toPoint())
                return
            return super().mousePressEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        item = self.itemAt(e.position().toPoint())
        if self.tool == "select" and isinstance(item, (AreaItem, _Handle)):
            return super().mousePressEvent(e)
        i, up = self.to_page(sp)
        if i < 0:
            return super().mousePressEvent(e)
        self._drag = (i, up, sp)
        if self.tool == "ink":
            self._ink_pts = [up]
            path = QPainterPath(sp)
            self._ink = QGraphicsPathItem(path)
            self._ink.setPen(QPen(QColor(30, 100, 220), 2))
            self._ink.setZValue(20)
            self.scene().addItem(self._ink)
        else:
            self._band = QGraphicsRectItem(QRectF(sp, sp))
            self._band.setPen(QPen(QColor(30, 100, 220), 0, Qt.DashLine))
            self._band.setBrush(QColor(30, 100, 220, 30))
            self._band.setZValue(20)
            self.scene().addItem(self._band)

    def mouseMoveEvent(self, e):
        if self._drag and (self._band or self._ink):
            sp = self.mapToScene(e.position().toPoint())
            if self._ink:
                p = self._ink.path()
                p.lineTo(sp)
                self._ink.setPath(p)
                i, up = self._drag[0], (sp - self.items[self._drag[0]].pos()) / (self.zoom * self.unit_scale)
                self._ink_pts.append(QPointF(up.x(), up.y()))
            else:
                if self.tool in ("line", "arrow"):
                    self._band.setRect(QRectF(self._drag[2], sp).normalized())
                else:
                    self._band.setRect(QRectF(self._drag[2], sp).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag and e.button() == Qt.LeftButton:
            i, up0, sp0 = self._drag
            sp = self.mapToScene(e.position().toPoint())
            self._drag = None
            if self._ink:
                self.scene().removeItem(self._ink)
                self._ink = None
                if len(self._ink_pts) > 1:
                    self.inkDrawn.emit(i, list(self._ink_pts))
                return
            if self._band:
                self.scene().removeItem(self._band)
                self._band = None
            s = self.zoom * self.unit_scale
            up1 = (sp - self.items[i].pos()) / s
            w, h = self.sizes[i]
            up1 = QPointF(min(max(0, up1.x()), w), min(max(0, up1.y()), h))
            if (sp - sp0).manhattanLength() < 4:
                self.clicked.emit(i, up0)
            elif self.tool in ("line", "arrow"):
                self.lineDrawn.emit(i, up0, up1)
            else:
                self.rectDrawn.emit(i, QRectF(up0, up1).normalized())
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        i, up = self.to_page(sp)
        if i >= 0:
            self.doubleClicked.emit(i, up)
        super().mouseDoubleClickEvent(e)


# ---------------------------------------------------------------- editable areas (OCR editor)
class _Handle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(-4, -4, 8, 8, parent)
        self.setBrush(Qt.white)
        self.setPen(QPen(parent.color, 0))
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, False)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setZValue(12)

    def mousePressEvent(self, e):
        self._start = e.scenePos()
        self._rect = self.parentItem().rect()

    def mouseMoveEvent(self, e):
        d = e.scenePos() - self._start
        r = QRectF(self._rect)
        r.setBottomRight(r.bottomRight() + d)
        r = r.normalized()
        if r.width() > 6 and r.height() > 6:
            self.parentItem().prepareGeometryChange()
            self.parentItem().setRect(r)
            self.setPos(r.bottomRight())

    def mouseReleaseEvent(self, e):
        self.parentItem().changed()


class AreaItem(QGraphicsRectItem):
    def __init__(self, view, page, index, kind, scene_rect, on_change, on_select, number=None):
        super().__init__(QRectF(0, 0, scene_rect.width(), scene_rect.height()))
        self.setPos(scene_rect.topLeft())
        self.view, self.page, self.index, self.kind = view, page, index, kind
        self.on_change, self.on_select = on_change, on_select
        self.color = AREA_COLORS.get(kind, QColor(80, 80, 80))
        c = QColor(self.color)
        c.setAlpha(28)
        self.setBrush(c)
        self.setPen(QPen(self.color, 2, Qt.DashLine if kind == "recognition" else Qt.SolidLine))
        self.pen().setCosmetic(True)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(10)
        self.handle = _Handle(self)
        self.handle.setPos(self.rect().bottomRight())
        self.number = number

    def paint(self, p, opt, w=None):
        if self.isSelected():
            c = QColor(self.color)
            c.setAlpha(60)
            self.setBrush(c)
        else:
            c = QColor(self.color)
            c.setAlpha(28)
            self.setBrush(c)
        super().paint(p, opt, w)
        if self.number is not None:
            p.setPen(Qt.white)
            r = QRectF(self.rect().topLeft(), QSize(22, 16))
            p.fillRect(r, self.color)
            p.setFont(QFont("Sans", 8, QFont.Bold))
            p.drawText(r, Qt.AlignCenter, str(self.number))

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.on_select(self.index)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.changed()

    def changed(self):
        s = self.view.zoom * self.view.unit_scale
        pp = self.view.items[self.page].pos()
        tl = (self.pos() + self.rect().topLeft() - pp) / s
        self.on_change(self.index, QRectF(tl.x(), tl.y(), self.rect().width() / s, self.rect().height() / s))


# ---------------------------------------------------------------- thumbnails
class ThumbnailList(QListWidget):
    moved = Signal(list)  # new order of original indices

    def __init__(self, parent=None, size=120):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.TopToBottom)
        self.setWrapping(False)
        self.setIconSize(QSize(size, size))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Snap)
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setUniformItemSizes(True)
        self.setMinimumWidth(size + 40)

    def dropEvent(self, e):
        super().dropEvent(e)
        order = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        self.moved.emit(order)

    def selected_indices(self):
        return sorted(self.row(it) for it in self.selectedItems())


# ---------------------------------------------------------------- languages
COMMON = ["eng", "ell", "eng+ell", "grc", "ell+grc", "deu", "fra", "spa", "ita", "rus"]


def lang_label(spec):
    return " + ".join(ocr.lang_name(c) for c in spec.split("+"))


class LanguageDialog(QDialog):
    def __init__(self, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Languages")
        self.resize(360, 460)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Select one or more OCR languages:"))
        self.flt = QLineEdit(placeholderText="Search...")
        v.addWidget(self.flt)
        self.lst = QListWidget()
        avail = ocr.available_languages()
        for code in sorted(avail, key=lambda c: ocr.lang_name(c)):
            it = QListWidgetItem(f"{ocr.lang_name(code)}  ({code})")
            it.setData(Qt.UserRole, code)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if code in selected else Qt.Unchecked)
            self.lst.addItem(it)
        v.addWidget(self.lst)
        from ..core.paths import user_tessdata_dir
        hint = QLabel(f"<small>More languages: copy <i>*.traineddata</i> files to<br>{user_tessdata_dir()}</small>")
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.flt.textChanged.connect(self._filter)

    def _filter(self, t):
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            it.setHidden(t.lower() not in it.text().lower())

    def selected(self):
        return [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count())
                if self.lst.item(i).checkState() == Qt.Checked]


class LanguageCombo(QComboBox):
    """Combo with common language sets and 'More languages...' (common sets plus a full picker)."""
    changed = Signal(list)

    def __init__(self, langs=None, parent=None, store=True):
        super().__init__(parent)
        self.store = store
        self.setMinimumWidth(170)
        self._cur = list(langs or settings()["languages"])
        self._fill()
        self.activated.connect(self._act)

    def _fill(self):
        self.blockSignals(True)
        self.clear()
        avail = set(ocr.available_languages())
        cur = "+".join(self._cur)
        specs = [cur] + [c for c in COMMON if c != cur and all(x in avail for x in c.split("+"))]
        for s in specs:
            self.addItem(lang_label(s), s)
        self.addItem("More languages...", "__more__")
        self.setCurrentIndex(0)
        self.blockSignals(False)

    def _act(self, idx):
        d = self.itemData(idx)
        if d == "__more__":
            dlg = LanguageDialog(self._cur, self)
            if dlg.exec() and dlg.selected():
                self._cur = dlg.selected()
        else:
            self._cur = d.split("+")
        if self.store:
            settings()["languages"] = self._cur
        self._fill()
        self.changed.emit(self._cur)

    def languages(self):
        return list(self._cur)

    def set_languages(self, langs):
        self._cur = list(langs)
        self._fill()


# ---------------------------------------------------------------- about
class AboutDialog(QDialog):
    def __init__(self, app_id, parent=None):
        super().__init__(parent)
        name = APPS[app_id][0]
        self.setWindowTitle(f"About {name}")
        h = QHBoxLayout(self)
        lbl = QLabel()
        lbl.setPixmap(app_icon(app_id).pixmap(96, 96))
        lbl.setAlignment(Qt.AlignTop)
        h.addWidget(lbl)
        v = QVBoxLayout()
        h.addLayout(v)
        from ..core import paths
        tv = "not found"
        try:
            import subprocess
            tb = paths.tesseract_bin()
            if tb:
                tv = subprocess.run([tb, "--version"], capture_output=True, text=True).stdout.split("\n")[0]
        except Exception:
            pass
        import pymupdf
        from PySide6 import __version__ as qv
        t = QTextBrowser()
        t.setOpenExternalLinks(True)
        t.setHtml(f"<h2>{name}</h2><p><b>Version {__version__}</b><br>{APPS[app_id][2]}</p>"
                  "<p>Part of the <b>Folio</b> suite for Linux: Folio Reader, Folio OCR Editor, "
                  "Folio Compare and Folio Snap.</p>"
                  "<p>Open-source OCR and PDF tools for the Linux desktop, built on Tesseract, "
                  "MuPDF and Qt.</p>"
                  f"<p><small>OCR engine: {tv}<br>PDF engine: PyMuPDF {pymupdf.VersionBind} / MuPDF {pymupdf.VersionFitz}<br>"
                  f"UI: Qt for Python {qv}<br>Installed OCR languages: {len(ocr.available_languages())}</small></p>")
        t.setMinimumSize(420, 260)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)
