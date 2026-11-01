"""Folio Snap — capture an area/window/screen, OCR it and send the result."""
import argparse
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGridLayout, QLabel, QMenu,
                               QPushButton, QSystemTrayIcon, QToolButton, QWidget)

from .. import APPS
from ..core import capture, exporters, ocr, paths
from ..core.ocr import build_table
from ..core.settings import settings
from ..ui.qtutil import app_icon, error, info, make_app, pil_to_pixmap, pil_to_qimage, run_task
from ..ui.widgets import AboutDialog, LanguageCombo

CAPTURE_MODES = ["Area", "Window", "Screen", "Timed Screen"]
SEND_MODES = ["Text to Clipboard", "Table to Clipboard", "Image to Clipboard", "Text to File", "Table to File",
              "Image to File", "Text to Microsoft Word", "Table to Microsoft Excel", "Image to OCR Editor",
              "Image to E-mail"]
SEND_LABELS = {"Text to Microsoft Word": "Text to Word Processor", "Table to Microsoft Excel": "Table to Spreadsheet"}


class SelectionOverlay(QWidget):
    """Frozen screenshot shown full screen; the user drags a rectangle (then double-clicks or presses Enter)."""

    def __init__(self, img, done):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.img = img
        self.pm = pil_to_pixmap(img)
        self.done = done
        self.sel = None
        self.start = None
        self.setCursor(Qt.CrossCursor)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen().geometry().center())
        cur = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        self.screen_geo = cur.geometry()
        virt = cur.virtualGeometry()
        # if the grab covers the whole virtual desktop, show only this screen's part
        if abs(self.pm.width() / cur.devicePixelRatio() - virt.width()) < 4 and len(QGuiApplication.screens()) > 1:
            dpr = self.pm.width() / virt.width()
            off = self.screen_geo.topLeft() - virt.topLeft()
            self.src = QRect(int(off.x() * dpr), int(off.y() * dpr), int(self.screen_geo.width() * dpr),
                             int(self.screen_geo.height() * dpr))
        else:
            self.src = QRect(0, 0, self.pm.width(), self.pm.height())
        self.setGeometry(self.screen_geo)

    def _scale(self):
        return self.src.width() / max(1, self.width()), self.src.height() / max(1, self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pm, self.src)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self.sel:
            sx, sy = self._scale()
            src = QRect(int(self.src.x() + self.sel.x() * sx), int(self.src.y() + self.sel.y() * sy),
                        int(self.sel.width() * sx), int(self.sel.height() * sy))
            p.drawPixmap(self.sel, self.pm, src)
            p.setPen(QPen(QColor(138, 90, 230), 2))
            p.drawRect(self.sel.adjusted(0, 0, -1, -1))
        p.setPen(Qt.white)
        p.setFont(QFont("Sans", 11))
        msg = ("Drag and resize selected area.\nRight-click or ESC to cancel, double click or Enter to capture."
               if self.sel and not self.start else
               "Left-click and drag to select an area on your screen.\nRight-click or press ESC to cancel capture.")
        r = QRect(0, 18, self.width(), 50)
        p.fillRect(QRect(self.width() // 2 - 300, 12, 600, 52), QColor(0, 0, 0, 160))
        p.drawText(r, Qt.AlignHCenter | Qt.AlignTop, msg)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.close()
            self.done(None)
            return
        self.start = e.position().toPoint()
        self.sel = QRect(self.start, self.start)
        self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.sel = QRect(self.start, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        self.start = None
        if self.sel and (self.sel.width() < 4 or self.sel.height() < 4):
            self.sel = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        self._accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            self.done(None)
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._accept()

    def _accept(self):
        if not self.sel:
            return
        sx, sy = self._scale()
        box = (int(self.src.x() + self.sel.x() * sx), int(self.src.y() + self.sel.y() * sy),
               int(self.src.x() + self.sel.right() * sx), int(self.src.y() + self.sel.bottom() * sy))
        crop = self.img.crop(box)
        self.close()
        self.done(crop)


class SnapWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window)
        self.setWindowTitle(APPS["snap"][0])
        self.setWindowIcon(app_icon("snap"))
        s = settings()["snap"]
        g = QGridLayout(self)
        g.setContentsMargins(10, 8, 10, 8)
        self.capture = QComboBox()
        self.capture.addItems(CAPTURE_MODES)
        self.capture.setCurrentText(s.get("capture", "Area"))
        self.lang = LanguageCombo(s.get("languages") or settings()["languages"], store=False)
        self.send = QComboBox()
        for m in SEND_MODES:
            self.send.addItem(SEND_LABELS.get(m, m), m)
        self.send.setCurrentIndex(max(0, self.send.findData(s.get("send", "Text to Clipboard"))))
        g.addWidget(QLabel("&Capture:", buddy=self.capture), 0, 0)
        g.addWidget(self.capture, 0, 1)
        g.addWidget(QLabel("&Language:", buddy=self.lang), 1, 0)
        g.addWidget(self.lang, 1, 1)
        g.addWidget(QLabel("&Send:", buddy=self.send), 2, 0)
        g.addWidget(self.send, 2, 1)
        self.go = QPushButton("&Go!")
        self.go.setMinimumSize(86, 86)
        f = self.go.font()
        f.setPointSize(f.pointSize() + 5)
        f.setBold(True)
        self.go.setFont(f)
        self.go.setStyleSheet("QPushButton{background:#8a5ae6;color:white;border-radius:10px}"
                              "QPushButton:hover{background:#7444d6}")
        g.addWidget(self.go, 0, 2, 3, 1)
        self.menu_btn = QToolButton(text="☰")
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        g.addWidget(self.menu_btn, 0, 3, 1, 1, Qt.AlignTop)
        self.go.clicked.connect(self.start_capture)
        for w in (self.capture, self.send):
            w.currentIndexChanged.connect(self._save)
        self.lang.changed.connect(lambda _: self._save())
        self._build_menu()
        self.setFixedSize(self.sizeHint())
        self._apply_on_top()

    # -- menus / tray
    def _build_menu(self):
        m = QMenu(self)
        m.addAction("&Text to Clipboard", lambda: self.quick("Text to Clipboard"))
        m.addAction("&Image to Clipboard", lambda: self.quick("Image to Clipboard"))
        m.addAction(f"&Show {APPS['snap'][0]}", self.show_window)
        m.addSeparator()
        opt = m.addMenu("&Options")
        s = settings()["snap"]
        self.a_top = opt.addAction("&Always on Top")
        self.a_top.setCheckable(True)
        self.a_top.setChecked(bool(s.get("always_on_top")))
        self.a_top.toggled.connect(self._toggle_top)
        self.a_start = opt.addAction("&Run at Startup")
        self.a_start.setCheckable(True)
        self.a_start.setChecked(autostart_file().exists())
        self.a_start.toggled.connect(set_autostart)
        self.a_sound = opt.addAction("Enable &Sounds")
        self.a_sound.setCheckable(True)
        self.a_sound.setChecked(bool(s.get("sounds")))
        self.a_sound.toggled.connect(lambda v: self._set("sounds", v))
        opt.addAction("&Keyboard Shortcut...", self.shortcut_help)
        h = m.addMenu("&Help")
        h.addAction("&Help", self.shortcut_help)
        h.addAction("&About", lambda: AboutDialog("snap", self).exec())
        m.addSeparator()
        m.addAction("E&xit", QApplication.quit)
        self.menu = m
        self.menu_btn.setMenu(m)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon("snap"), self)
            self.tray.setToolTip(APPS["snap"][0])
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda r: self.show_window() if r == QSystemTrayIcon.Trigger else None)
            self.tray.show()

    def _set(self, k, v):
        s = settings()
        s.data["snap"][k] = v
        s.save()

    def _save(self):
        s = settings()
        s.data["snap"].update({"capture": self.capture.currentText(), "send": self.send.currentData(),
                               "languages": self.lang.languages()})
        s.save()

    def _toggle_top(self, v):
        self._set("always_on_top", v)
        self._apply_on_top()

    def _apply_on_top(self):
        on = bool(settings()["snap"].get("always_on_top"))
        vis = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if vis:
            self.show()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def shortcut_help(self):
        cmd = os.environ.get("APPIMAGE") or "folio-snap"
        info(self, "Global hot keys are managed by the desktop on Linux (Wayland does not let apps grab keys).\n\n"
                   "Add a custom shortcut in Settings → Keyboard → Custom Shortcuts with the command:\n\n"
                   f"    {cmd} --go\n\n"
                   "Options: --capture area|window|screen|timed   --send \"Text to Clipboard\"   --lang eng+ell")

    def notify(self, msg):
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APPS["snap"][0], msg, app_icon("snap"), 3500)
        if settings()["snap"].get("sounds"):
            QApplication.beep()

    # -- capture flow
    def quick(self, send):
        self.send.setCurrentIndex(self.send.findData(send))
        self.start_capture()

    def start_capture(self, mode=None):
        mode = mode or self.capture.currentText()
        self._was_visible = self.isVisible()
        self.hide()
        QTimer.singleShot(350, lambda: self._grab(mode))

    def _grab(self, mode):
        try:
            if mode == "Window":
                img = capture.grab_window_interactive()
                self._got(img)
            elif mode == "Timed Screen":
                self._countdown(5)
            elif mode == "Screen":
                self._got(capture.grab_screen())
            else:
                img = capture.grab_screen()
                self.overlay = SelectionOverlay(img, self._got)
                if capture.is_wayland():
                    self.overlay.showFullScreen()
                else:
                    self.overlay.show()
                self.overlay.activateWindow()
                self.overlay.raise_()
        except capture.CaptureError as e:
            self._restore()
            if str(e) != "cancelled":
                error(None, e)

    def _countdown(self, n):
        if n <= 0:
            try:
                self._got(capture.grab_screen())
            except capture.CaptureError as e:
                self._restore()
                error(None, e)
            return
        if self.tray and n == 5:
            self.tray.showMessage(APPS["snap"][0], "Capturing the screen in 5 seconds...", app_icon("snap"), 2000)
        QTimer.singleShot(1000, lambda: self._countdown(n - 1))

    def _restore(self):
        if getattr(self, "_was_visible", True) or not self.tray:
            self.show_window()

    def _got(self, img):
        if img is None:
            self._restore()
            return
        self.last_image = img
        send = self.send.currentData()
        if send.startswith("Image"):
            self._send(send, img, None)
            return
        langs = self.lang.languages()
        if not ocr.available():
            self._restore()
            error(self, "The OCR engine (tesseract) was not found.")
            return
        img.info["dpi"] = (96, 96)

        def work(progress, cancel):
            from ..core.imageproc import set_resolution
            big = set_resolution(img.convert("RGB"), 300)  # screen text is small; upscale for OCR
            res = ocr.recognize(big, langs, psm="auto")
            return res
        run_task(None, "Processing...", work, on_done=lambda res: self._send(send, img, res),
                 on_error=lambda m: (self._restore(), error(self, m)))

    def _send(self, send, img, res):
        cb = QGuiApplication.clipboard()
        text = res.text(keep_line_breaks=True) if res else ""
        table = ""
        if res:
            lines = [l for b in res.blocks for l in b.lines]
            table = "\n".join("\t".join(r) for r in build_table(lines))
        try:
            if send == "Text to Clipboard":
                cb.setText(text)
                self.notify("The text from the selected area has been copied to the Clipboard.")
            elif send == "Table to Clipboard":
                cb.setText(table)
                self.notify("The table from the selected area has been copied to the Clipboard.")
            elif send == "Image to Clipboard":
                cb.setImage(pil_to_qimage(img))
                self.notify("The selected area has been copied to the Clipboard.")
            elif send in ("Text to File", "Table to File", "Image to File"):
                self._save_file(send, img, res)
            elif send == "Text to Microsoft Word":
                out = Path(tempfile.mkdtemp(prefix="folio-snap-")) / "Captured text.odt"
                exporters.export([exporters.ExportPage(img, res)], "odt", str(out))
                open_office(out)
                self.notify("The text from the selected area has been sent to the word processor.")
            elif send == "Table to Microsoft Excel":
                out = Path(tempfile.mkdtemp(prefix="folio-snap-")) / "Captured table.xlsx"
                exporters.export([exporters.ExportPage(img, res)], "xlsx", str(out))
                open_office(out)
                self.notify("The text from the selected area has been sent to the spreadsheet.")
            elif send == "Image to OCR Editor":
                out = Path(tempfile.mkdtemp(prefix="folio-snap-")) / "Screenshot.png"
                img.save(out)
                paths.launch_sibling("ocr", out)
                self.notify("The selected area has been sent to OCR Editor.")
            elif send == "Image to E-mail":
                out = Path(tempfile.mkdtemp(prefix="folio-snap-")) / "Screenshot.png"
                img.save(out)
                paths.run_external(["xdg-email", "--subject", "Screenshot", "--attach", str(out)])
        except Exception as e:
            error(self, f"Operation failed. Some errors occurred:\n{e}")
        self._restore()

    def _save_file(self, send, img, res):
        if send == "Image to File":
            fn, flt = QFileDialog.getSaveFileName(self, "Save Image As...", str(Path.home() / "Screenshot.png"),
                                                  "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp);;TIFF (*.tif)")
            if fn:
                if not Path(fn).suffix:
                    fn += ".png"
                im = img.convert("RGB") if fn.lower().endswith((".jpg", ".jpeg")) else img
                im.save(fn)
            return
        if send == "Table to File":
            fn, flt = QFileDialog.getSaveFileName(self, "Save Table As...", str(Path.home() / "Table.xlsx"),
                                                  "Excel Workbook (*.xlsx);;CSV Document (*.csv)")
        else:
            fn, flt = QFileDialog.getSaveFileName(self, "Save Text As...", str(Path.home() / "Text.txt"),
                                                  "Text (*.txt);;Rich Text Format (*.rtf);;Word Document (*.docx);;ODT Document (*.odt)")
        if not fn:
            return
        ext = Path(fn).suffix.lower().lstrip(".")
        if not ext:
            ext = "xlsx" if send == "Table to File" else "txt"
            fn += "." + ext
        page = exporters.ExportPage(img, res)
        if send == "Table to File" and res:
            for b in res.blocks:
                b.kind, b.rows = "table", build_table(b.lines)
        exporters.export([page], ext, fn)
        if self.tray:
            self.notify(f"Saved {Path(fn).name}")


def open_office(path):
    if paths.which_external("libreoffice"):
        paths.run_external(["libreoffice", str(path)])
    else:
        paths.open_with_default(path)


def autostart_file():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "folio-snap.desktop"


def set_autostart(on):
    f = autostart_file()
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        exe = os.environ.get("APPIMAGE")
        cmd = f'"{exe}" --tray' if exe else " ".join(paths.sibling_command("snap")) + " --tray"
        f.write_text("[Desktop Entry]\nType=Application\nName=Folio Snap\n"
                     f"Exec={cmd}\nIcon=folio-snap\nX-GNOME-Autostart-enabled=true\n")
        info(None, "Now Folio Snap will start automatically when you log in.")
    elif f.exists():
        f.unlink()


def run(argv):
    ap = argparse.ArgumentParser(prog="folio-snap")
    ap.add_argument("--go", action="store_true", help="capture immediately using saved settings")
    ap.add_argument("--capture", choices=["area", "window", "screen", "timed"])
    ap.add_argument("--send", help="send mode, e.g. 'Text to Clipboard'")
    ap.add_argument("--lang", help="OCR languages, e.g. eng+ell")
    ap.add_argument("--tray", action="store_true", help="start minimized to the tray")
    args, rest = ap.parse_known_args(argv)
    app = make_app("snap", rest)
    app.setQuitOnLastWindowClosed(False)
    w = SnapWindow()
    if args.lang:
        w.lang.set_languages(args.lang.split("+"))
    if args.send:
        i = w.send.findData(args.send)
        if i >= 0:
            w.send.setCurrentIndex(i)
    mode = {"area": "Area", "window": "Window", "screen": "Screen", "timed": "Timed Screen"}.get(args.capture or "")
    if mode:
        w.capture.setCurrentText(mode)
    if args.go or args.capture:
        w._was_visible = False
        QTimer.singleShot(200, lambda: w.start_capture(mode))
        if not w.tray:
            # one-shot: quit when the capture flow is finished
            app.setQuitOnLastWindowClosed(True)
    elif not (args.tray and w.tray):
        w.show()
    if not w.tray:
        app.setQuitOnLastWindowClosed(True)
    return app.exec()
