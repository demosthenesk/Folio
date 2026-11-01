"""Qt helpers shared by all Folio apps: application setup, icons, image conversion, background tasks."""
import os
import sys
import traceback
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QStyle

from .. import APPS, __version__

RES = Path(__file__).resolve().parent.parent / "resources"


def app_icon(app_id):
    p = RES / "icons" / f"{APPS[app_id][1]}.svg"
    return QIcon(str(p)) if p.exists() else QIcon()


def make_app(app_id, argv):
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication([sys.argv[0]] + [a for a in argv if a.startswith("-") and a.startswith("-platform")])
    name, binary, _ = APPS[app_id]
    app.setApplicationName(name)
    app.setApplicationDisplayName(name)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Folio")
    app.setDesktopFileName(binary)
    app.setWindowIcon(app_icon(app_id))
    if not QIcon.themeName():
        QIcon.setThemeName("Yaru")
    QIcon.setFallbackThemeName("Adwaita")
    return app


# ---------------------------------------------------------------- icons
_STD = {
    "document-open": QStyle.SP_DialogOpenButton, "document-save": QStyle.SP_DialogSaveButton,
    "go-next": QStyle.SP_ArrowForward, "go-previous": QStyle.SP_ArrowBack, "go-up": QStyle.SP_ArrowUp,
    "go-down": QStyle.SP_ArrowDown, "edit-delete": QStyle.SP_TrashIcon, "view-refresh": QStyle.SP_BrowserReload,
    "help-about": QStyle.SP_MessageBoxInformation, "dialog-cancel": QStyle.SP_DialogCancelButton,
    "process-stop": QStyle.SP_BrowserStop, "document-new": QStyle.SP_FileIcon, "folder": QStyle.SP_DirIcon,
    "media-playback-start": QStyle.SP_MediaPlay, "window-close": QStyle.SP_TitleBarCloseButton,
}

_SVG = {
    "tool-select": '<path d="M5 3l12 9-5.5 1 3 6-2.5 1.2-3-6L5 18z" fill="#333"/>',
    "tool-hand": '<path d="M8 11V5.5a1.5 1.5 0 0 1 3 0V10V4a1.5 1.5 0 0 1 3 0v6V5.5a1.5 1.5 0 0 1 3 0V13c0 4-2 7-6 7-3 0-4.5-2-6-5l-1.5-3a1.4 1.4 0 0 1 2.4-1.4L8 13z" fill="#f2d2b0" stroke="#555" stroke-width="1"/>',
    "area-text": '<rect x="3" y="3" width="18" height="18" fill="#e8f4e8" stroke="#2e9b4a" stroke-width="1.6"/><path d="M7 8h10M7 12h10M7 16h7" stroke="#2e9b4a" stroke-width="1.6"/>',
    "area-table": '<rect x="3" y="3" width="18" height="18" fill="#e8eef9" stroke="#3a64c8" stroke-width="1.6"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18" stroke="#3a64c8" stroke-width="1.2"/>',
    "area-picture": '<rect x="3" y="3" width="18" height="18" fill="#fbecec" stroke="#c63c3c" stroke-width="1.6"/><path d="M5 18l5-6 3 4 2-2 4 4z" fill="#c63c3c"/><circle cx="15.5" cy="8" r="2" fill="#c63c3c"/>',
    "area-background": '<rect x="3" y="3" width="18" height="18" fill="#f6eefb" stroke="#8a4cc0" stroke-width="1.6"/><path d="M5 18l5-6 3 4 2-2 4 4z" fill="#d9c0ee"/><path d="M7 8h10" stroke="#8a4cc0" stroke-width="1.6"/>',
    "area-barcode": '<rect x="3" y="3" width="18" height="18" fill="#fff6e0" stroke="#c88a00" stroke-width="1.6"/><path d="M6 6v12M8 6v12M11 6v12M12.5 6v12M15 6v12M18 6v12" stroke="#333" stroke-width="1.1"/>',
    "area-recognition": '<rect x="3" y="3" width="18" height="18" fill="#fff" stroke="#666" stroke-dasharray="3 2" stroke-width="1.6"/><text x="6.5" y="16.5" font-size="10" font-family="sans-serif" fill="#444">A</text>',
    "recognize": '<rect x="3" y="3" width="13" height="17" rx="1.5" fill="#fff" stroke="#444" stroke-width="1.3"/><path d="M6 7h7M6 10h7M6 13h5" stroke="#444" stroke-width="1.2"/><circle cx="16" cy="16" r="4.5" fill="#33b36b"/><path d="M14 16l1.5 1.5 3-3" stroke="#fff" stroke-width="1.5" fill="none"/>',
    "analyze": '<rect x="3" y="3" width="18" height="18" fill="#fff" stroke="#444" stroke-width="1.3"/><rect x="5" y="5" width="14" height="5" fill="none" stroke="#2e9b4a" stroke-width="1.4"/><rect x="5" y="12" width="6" height="7" fill="none" stroke="#c63c3c" stroke-width="1.4"/><rect x="13" y="12" width="6" height="7" fill="none" stroke="#3a64c8" stroke-width="1.4"/>',
    "verify": '<path d="M4 6h10M4 10h10M4 14h6" stroke="#444" stroke-width="1.5"/><path d="M12 17l3 3 6-7" stroke="#2e9b4a" stroke-width="2.2" fill="none"/>',
    "highlight": '<rect x="3" y="15" width="18" height="5" fill="#ffe14d"/><path d="M8 13l7-9 3 2-7 9z" fill="#555"/>',
    "underline": '<text x="6" y="15" font-size="13" font-family="serif" fill="#333">U</text><path d="M4 19h16" stroke="#2a7de1" stroke-width="2.2"/>',
    "strikethrough": '<text x="6" y="17" font-size="13" font-family="serif" fill="#333">S</text><path d="M3 12h18" stroke="#e03c31" stroke-width="2.2"/>',
    "note": '<path d="M4 4h16v12h-9l-4 4v-4H4z" fill="#ffe680" stroke="#b08a00" stroke-width="1.3"/>',
    "ink": '<path d="M3 17c3-8 5 2 8-4s4-8 7-3 2 5 3 6" stroke="#2a7de1" stroke-width="2" fill="none" stroke-linecap="round"/>',
    "line": '<path d="M4 20L20 4" stroke="#333" stroke-width="2"/>',
    "arrow": '<path d="M4 20L19 5M19 5h-7M19 5v7" stroke="#333" stroke-width="2" fill="none"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" fill="none" stroke="#333" stroke-width="2"/>',
    "oval": '<ellipse cx="12" cy="12" rx="8.5" ry="6" fill="none" stroke="#333" stroke-width="2"/>',
    "textbox": '<rect x="3" y="5" width="18" height="14" fill="#fff" stroke="#333" stroke-width="1.5"/><text x="7" y="16.5" font-size="11" font-family="sans-serif" fill="#333">T</text>',
    "stamp": '<path d="M9 3h6v6l3 3v3H6v-3l3-3z" fill="#e8543f"/><rect x="4" y="17" width="16" height="3" fill="#b8281a"/>',
    "signature": '<path d="M3 16c2-6 4-10 5-6s-2 8 1 6 3-5 5-3 2 3 7 0" stroke="#1a56b0" stroke-width="1.8" fill="none"/><path d="M3 20h18" stroke="#666" stroke-width="1"/>',
    "redact": '<rect x="3" y="8" width="18" height="8" fill="#111"/><path d="M5 5h9M5 19h12" stroke="#777" stroke-width="1.5"/>',
    "edit-text": '<path d="M4 6h10M4 10h7M4 14h5" stroke="#444" stroke-width="1.5"/><path d="M13 19l1-4 6-6 3 3-6 6z" fill="#f2b31b" stroke="#555"/>',
    "erase": '<path d="M7 20l-4-4 9-9 7 7-6 6z" fill="#f4a3b0" stroke="#555"/><path d="M3 21h18" stroke="#555"/>',
    "compare": '<rect x="2.5" y="4" width="8" height="16" fill="#cfe1fb" stroke="#1a56b0"/><rect x="13.5" y="4" width="8" height="16" fill="#cfe1fb" stroke="#1a56b0"/><path d="M4.5 8h4M15.5 8h4M4.5 12h4" stroke="#1a56b0"/><path d="M15.5 12h4" stroke="#e8543f" stroke-width="1.5"/>',
    "capture": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" stroke="#5a2fb5" stroke-width="2" fill="none"/><circle cx="12" cy="12" r="3" fill="#8a5ae6"/>',
    "scan": '<rect x="2" y="12" width="20" height="7" rx="1.5" fill="#ddd" stroke="#555"/><path d="M5 12l2-6h10l2 6" fill="#f4f4f4" stroke="#555"/><path d="M4 15h16" stroke="#33b36b" stroke-width="1.5"/>',
    "zoom-in": '<circle cx="10" cy="10" r="6" fill="none" stroke="#333" stroke-width="2"/><path d="M15 15l5 5M7 10h6M10 7v6" stroke="#333" stroke-width="2"/>',
    "zoom-out": '<circle cx="10" cy="10" r="6" fill="none" stroke="#333" stroke-width="2"/><path d="M15 15l5 5M7 10h6" stroke="#333" stroke-width="2"/>',
    "fit-width": '<path d="M3 12h18M6 9l-3 3 3 3M18 9l3 3-3 3" stroke="#333" stroke-width="1.8" fill="none"/><path d="M3 4v16M21 4v16" stroke="#999"/>',
    "fit-page": '<rect x="6" y="3" width="12" height="18" fill="none" stroke="#333" stroke-width="1.8"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#999"/>',
    "rotate-right": '<path d="M18 9a7 7 0 1 0 1 6" stroke="#333" stroke-width="2" fill="none"/><path d="M19 3v6h-6" stroke="#333" stroke-width="2" fill="none"/>',
    "rotate-left": '<path d="M6 9a7 7 0 1 1-1 6" stroke="#333" stroke-width="2" fill="none"/><path d="M5 3v6h6" stroke="#333" stroke-width="2" fill="none"/>',
    "deskew": '<path d="M5 18L19 14M5 13l14-4M5 8l14-4" stroke="#333" stroke-width="1.8"/>',
    "crop": '<path d="M6 2v16h16M2 6h16v16" stroke="#333" stroke-width="2" fill="none"/>',
    "image-edit": '<rect x="3" y="4" width="18" height="16" rx="1.5" fill="#fff" stroke="#444"/><path d="M5 18l5-6 3 4 2-2 4 4z" fill="#3d8ae8"/><circle cx="16" cy="8" r="2" fill="#f2b31b"/>',
    "send": '<path d="M3 12l18-8-6 16-3-6z" fill="#3d8ae8"/><path d="M12 14l9-10" stroke="#fff"/>',
    "pages": '<rect x="7" y="3" width="12" height="15" fill="#fff" stroke="#555"/><rect x="4" y="6" width="12" height="15" fill="#fff" stroke="#333"/>',
    "bookmark": '<path d="M7 3h10v18l-5-4-5 4z" fill="#3d8ae8"/>',
    "search": '<circle cx="10" cy="10" r="6" fill="none" stroke="#333" stroke-width="2"/><path d="M15 15l5 5" stroke="#333" stroke-width="2.5"/>',
    "attachment": '<path d="M16 7l-7 7a2 2 0 0 0 3 3l7-7a4 4 0 0 0-6-6l-7 7a6 6 0 0 0 8 8l5-5" stroke="#333" stroke-width="1.8" fill="none"/>',
    "comments": '<path d="M3 5h18v11h-8l-5 4v-4H3z" fill="#fff4b3" stroke="#a88a00"/>',
    "password": '<rect x="5" y="11" width="14" height="10" rx="1.5" fill="#f2b31b"/><path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="#555" stroke-width="2" fill="none"/>',
    "watermark": '<text x="2" y="17" font-size="10" font-family="sans-serif" fill="#999" transform="rotate(-25 12 12)">DRAFT</text>',
    "header-footer": '<rect x="4" y="2" width="16" height="20" fill="#fff" stroke="#444"/><path d="M6 5h12M6 19h12" stroke="#3d8ae8" stroke-width="2"/><path d="M6 9h12M6 12h12M6 15h8" stroke="#bbb"/>',
    "picture-add": '<rect x="3" y="5" width="15" height="13" fill="#fff" stroke="#444"/><path d="M5 16l4-5 3 3 2-2 3 4z" fill="#3d8ae8"/><path d="M19 1v7M15.5 4.5h7" stroke="#33b36b" stroke-width="2"/>',
    "link": '<path d="M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1" stroke="#1a56b0" stroke-width="2" fill="none"/>',
    "print": '<rect x="6" y="3" width="12" height="6" fill="#fff" stroke="#444"/><rect x="3" y="9" width="18" height="8" rx="1.5" fill="#ddd" stroke="#444"/><rect x="6" y="14" width="12" height="7" fill="#fff" stroke="#444"/>',
    "undo": '<path d="M9 7H15a5 5 0 0 1 0 10H8" stroke="#333" stroke-width="2" fill="none"/><path d="M10 3L5 7l5 4" stroke="#333" stroke-width="2" fill="none"/>',
    "redo": '<path d="M15 7H9a5 5 0 0 0 0 10h7" stroke="#333" stroke-width="2" fill="none"/><path d="M14 3l5 4-5 4" stroke="#333" stroke-width="2" fill="none"/>',
    "page-add": '<rect x="4" y="3" width="13" height="17" fill="#fff" stroke="#444"/><path d="M19 13v8M15 17h8" stroke="#33b36b" stroke-width="2.2"/>',
    "page-delete": '<rect x="4" y="3" width="13" height="17" fill="#fff" stroke="#444"/><path d="M15 15l6 6M21 15l-6 6" stroke="#e03c31" stroke-width="2.2"/>',
    "ocr-editor": '<rect x="3" y="3" width="18" height="18" rx="3" fill="#33b36b"/><text x="5" y="16.5" font-size="11" font-weight="bold" font-family="serif" fill="#fff">Aa</text>',
    "doc-word": '<rect x="3" y="3" width="18" height="18" rx="3" fill="#2b6cd4"/><text x="5.2" y="16.5" font-size="11" font-weight="bold" font-family="sans-serif" fill="#fff">W</text>',
    "doc-excel": '<rect x="3" y="3" width="18" height="18" rx="3" fill="#1f8a4c"/><path d="M7 7l10 10M17 7L7 17" stroke="#fff" stroke-width="2.4"/>',
    "doc-other": '<rect x="3" y="3" width="18" height="18" rx="3" fill="#7a5ae0"/><path d="M7 9h10M7 12h10M7 15h6" stroke="#fff" stroke-width="1.6"/>',
    "pdf-editor": '<rect x="3" y="3" width="18" height="18" rx="3" fill="#e8543f"/><text x="4.2" y="15.5" font-size="7.5" font-weight="bold" font-family="sans-serif" fill="#fff">PDF</text>',
}


def svg_icon(name, size=24):
    from PySide6.QtSvg import QSvgRenderer
    body = _SVG[name]
    data = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'.encode()
    r = QSvgRenderer(data)
    icon = QIcon()
    for s in (16, 24, 32, 48):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        r.render(p)
        p.end()
        icon.addPixmap(pm)
    return icon


_cache = {}


def icon(name, *theme_names):
    key = (name,) + theme_names
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    if name in _SVG:
        ic = svg_icon(name)
    else:
        for t in (name,) + theme_names:
            if QIcon.hasThemeIcon(t):
                ic = QIcon.fromTheme(t)
                break
        if ic.isNull() and name in _STD and QApplication.instance():
            ic = QApplication.style().standardIcon(_STD[name])
    _cache[key] = ic
    return ic


# ---------------------------------------------------------------- image conversion
def pil_to_qimage(img: Image.Image) -> QImage:
    if img.mode == "1":
        img = img.convert("L")
    if img.mode == "L":
        data = img.tobytes()
        q = QImage(data, img.width, img.height, img.width, QImage.Format_Grayscale8)
        return q.copy()
    if img.mode != "RGB":
        img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    q = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return q.copy()


def pil_to_pixmap(img):
    return QPixmap.fromImage(pil_to_qimage(img))


def qimage_to_pil(q: QImage) -> Image.Image:
    q = q.convertToFormat(QImage.Format_RGB888)
    w, h = q.width(), q.height()
    ptr = q.constBits()
    bpl = q.bytesPerLine()
    raw = bytes(ptr)[: bpl * h]
    img = Image.frombuffer("RGB", (w, h), raw, "raw", "RGB", bpl, 1)
    return img.copy()


def thumbnail_icon(img: Image.Image, size=160):
    t = img.copy()
    t.thumbnail((size, size))
    pm = pil_to_pixmap(t)
    return QIcon(pm)


# ---------------------------------------------------------------- background work
class _Signals(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str, str)


class Task(QRunnable):
    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.signals = _Signals()
        self.cancelled = False
        self.setAutoDelete(False)

    def run(self):
        try:
            res = self.fn(*self.args, progress=self._progress, cancel=lambda: self.cancelled, **self.kw)
            self.signals.done.emit(res)
        except Exception as e:
            self.signals.failed.emit(str(e) or e.__class__.__name__, traceback.format_exc())

    def _progress(self, i, n, msg=""):
        self.signals.progress.emit(int(i), int(n), msg)


_running = set()


class _Relay(QObject):
    """Lives in the GUI thread so worker signals are delivered there (queued)."""

    def __init__(self, task, dlg, on_done, on_error, parent):
        super().__init__()
        self.task, self.dlg, self.on_done, self.on_error, self.parent_w = task, dlg, on_done, on_error, parent

    def progress(self, i, n, msg):
        d = self.dlg
        if d is None:
            return
        if msg:
            d.setLabelText(msg)
        d.setMaximum(max(n, 1))
        d.setValue(min(i, n))

    def _close(self):
        _running.discard(self)
        if self.dlg:
            try:
                self.dlg.canceled.disconnect()
            except Exception:
                pass
            self.dlg.reset()
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None

    def done(self, res):
        self._close()
        if self.task.cancelled:
            return
        if self.on_done:
            self.on_done(res)

    def failed(self, msg, tb):
        self._close()
        if self.task.cancelled or msg in ("Cancelled", "Cancelled()"):
            return
        sys.stderr.write(tb)
        if self.on_error:
            self.on_error(msg)
        else:
            QMessageBox.critical(self.parent_w, QApplication.applicationName(), msg)


def run_task(parent, title, fn, *args, on_done=None, on_error=None, modal=True, **kw):
    """Run fn(*args, progress=, cancel=) in a worker thread with a progress dialog."""
    task = Task(fn, *args, **kw)
    dlg = None
    if modal:
        dlg = QProgressDialog(title, "&Stop", 0, 0, parent)
        dlg.setWindowTitle("Processing...")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        dlg.setMinimumWidth(380)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(lambda: setattr(task, "cancelled", True))
    relay = _Relay(task, dlg, on_done, on_error, parent)
    task.signals.progress.connect(relay.progress, Qt.QueuedConnection)
    task.signals.done.connect(relay.done, Qt.QueuedConnection)
    task.signals.failed.connect(relay.failed, Qt.QueuedConnection)
    _running.add(relay)
    task.relay = relay
    QThreadPool.globalInstance().start(task)
    return task


def wait_tasks(timeout=600):
    """Block (processing events) until background tasks finished — used by tests and CLI modes."""
    import time
    t = time.time()
    app = QApplication.instance()
    while _running and time.time() - t < timeout:
        app.processEvents()
        time.sleep(0.02)


def error(parent, msg):
    QMessageBox.critical(parent, QApplication.applicationName(), str(msg))


def info(parent, msg):
    QMessageBox.information(parent, QApplication.applicationName(), str(msg))


def ask(parent, msg, buttons=QMessageBox.Yes | QMessageBox.No):
    return QMessageBox.question(parent, QApplication.applicationName(), msg, buttons)
