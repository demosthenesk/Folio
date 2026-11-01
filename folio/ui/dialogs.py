"""Shared dialogs: Options, Scan, page ranges, save-as helpers, recognition settings."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core import exporters, scanner
from ..core.settings import settings
from .qtutil import error, pil_to_pixmap, run_task, thumbnail_icon
from .widgets import LanguageCombo


class OptionsDialog(QDialog):
    """Options (General / Image Processing / Recognition / Format Settings)."""

    def __init__(self, parent=None, tab=0):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.resize(560, 470)
        s = settings()
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs)

        # General
        g = QWidget()
        gl = QVBoxLayout(g)
        box = QGroupBox("When OCR Editor starts")
        bl = QVBoxLayout(box)
        self.st_new = QRadioButton("&Open a new OCR project")
        self.st_last = QRadioButton("Open &the last used OCR project")
        (self.st_last if s["ocr_startup"] == "last" else self.st_new).setChecked(True)
        bl.addWidget(self.st_new)
        bl.addWidget(self.st_last)
        gl.addWidget(box)
        box = QGroupBox("Default OCR languages")
        bl = QHBoxLayout(box)
        self.langs = LanguageCombo(store=False)
        bl.addWidget(QLabel("OCR language&s:"))
        bl.addWidget(self.langs, 1)
        gl.addWidget(box)
        box = QGroupBox("Select device for acquiring images")
        bl = QFormLayout(box)
        self.dev = QLineEdit(s["scanner"].get("device", ""))
        self.dev.setPlaceholderText("default SANE device")
        bl.addRow("Scanner:", self.dev)
        gl.addWidget(box)
        gl.addStretch()
        tabs.addTab(g, "General")

        # Image processing
        ip = QWidget()
        il = QVBoxLayout(ip)
        pre = s["preprocess"]
        box = QGroupBox("Image processing settings (applied when adding images)")
        bl = QVBoxLayout(box)
        self.p_split = QCheckBox("Split &facing pages")
        self.p_orient = QCheckBox("Correct page &orientation")
        self.p_deskew = QCheckBox("&Deskew images and correct image resolution")
        self.p_noise = QCheckBox("Remove &noise (median filter)")
        for cb, k in ((self.p_split, "split_pages"), (self.p_orient, "orientation"),
                      (self.p_deskew, "deskew"), (self.p_noise, "denoise")):
            cb.setChecked(bool(pre.get(k)))
            bl.addWidget(cb)
        il.addWidget(box)
        box = QGroupBox("Resolution")
        bl = QFormLayout(box)
        self.dpi = QSpinBox(minimum=100, maximum=600, singleStep=50, value=int(s["ocr_dpi"]))
        bl.addRow("Render PDF pages for OCR at (dpi):", self.dpi)
        il.addWidget(box)
        il.addStretch()
        tabs.addTab(ip, "Image Processing")

        # Recognition
        rc = QWidget()
        rl = QVBoxLayout(rc)
        box = QGroupBox("&PDF recognition mode")
        bl = QVBoxLayout(box)
        self.pm = {k: QRadioButton(t) for k, t in (
            ("auto", "Automatically choose between OCR and text from PDF"),
            ("ocr", "Use OCR"), ("text", "Use only text from PDF"))}
        for k, rb in self.pm.items():
            rb.setChecked(s["pdf_mode"] == k)
            bl.addWidget(rb)
        rl.addWidget(box)
        box = QGroupBox("OCR speed and accuracy")
        bl = QHBoxLayout(box)
        self.thorough = QRadioButton("Thorough re&cognition")
        self.fast = QRadioButton("&Fast recognition")
        (self.fast if s["recognition_mode"] == "fast" else self.thorough).setChecked(True)
        bl.addWidget(self.thorough)
        bl.addWidget(self.fast)
        rl.addWidget(box)
        box = QGroupBox("Uncertain characters")
        bl = QFormLayout(box)
        self.unc = QSpinBox(minimum=0, maximum=100, value=int(s["uncertain_threshold"]))
        bl.addRow("Highlight words with confidence below (%):", self.unc)
        rl.addWidget(box)
        rl.addStretch()
        tabs.addTab(rc, "Recognition")

        # Format settings
        fs = QWidget()
        fl = QVBoxLayout(fs)
        box = QGroupBox("Document layout")
        bl = QVBoxLayout(box)
        self.klb = QCheckBox("&Keep line breaks")
        self.klb.setChecked(s["keep_line_breaks"])
        self.kpb = QCheckBox("Keep &page breaks")
        self.kpb.setChecked(s["keep_page_breaks"])
        self.kimg = QCheckBox("Keep &pictures")
        self.kimg.setChecked(s["export_images"])
        for w in (self.klb, self.kpb, self.kimg):
            bl.addWidget(w)
        fl.addWidget(box)
        box = QGroupBox("PDF")
        bl = QFormLayout(box)
        self.q = QComboBox()
        for k, t in (("high", "Best quality"), ("balanced", "Balanced"), ("compact", "Compact size")):
            self.q.addItem(t, k)
        self.q.setCurrentIndex(max(0, self.q.findData(s["pdf_image_quality"])))
        bl.addRow("Picture &quality:", self.q)
        fl.addWidget(box)
        fl.addStretch()
        tabs.addTab(fs, "Format Settings")
        tabs.setCurrentIndex(tab)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def save(self):
        s = settings()
        s.data["ocr_startup"] = "last" if self.st_last.isChecked() else "new"
        s.data["languages"] = self.langs.languages()
        s.data["scanner"]["device"] = self.dev.text().strip()
        s.data["preprocess"] = {"split_pages": self.p_split.isChecked(), "orientation": self.p_orient.isChecked(),
                                "deskew": self.p_deskew.isChecked(), "denoise": self.p_noise.isChecked()}
        s.data["ocr_dpi"] = self.dpi.value()
        s.data["pdf_mode"] = next(k for k, rb in self.pm.items() if rb.isChecked())
        s.data["recognition_mode"] = "fast" if self.fast.isChecked() else "thorough"
        s.data["uncertain_threshold"] = self.unc.value()
        s.data["keep_line_breaks"] = self.klb.isChecked()
        s.data["keep_page_breaks"] = self.kpb.isChecked()
        s.data["export_images"] = self.kimg.isChecked()
        s.data["pdf_image_quality"] = self.q.currentData()
        s.save()
        self.accept()


class ScanDialog(QDialog):
    """Acquire one or more pages from a SANE scanner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan")
        self.resize(520, 420)
        self.images = []
        v = QVBoxLayout(self)
        if not scanner.available():
            v.addWidget(QLabel("Scanning requires SANE.\nInstall it with:  sudo apt install sane-utils"))
            bb = QDialogButtonBox(QDialogButtonBox.Close)
            bb.rejected.connect(self.reject)
            v.addWidget(bb)
            return
        f = QFormLayout()
        self.dev = QComboBox()
        self.dev.addItem("(default device)", settings()["scanner"].get("device", ""))
        f.addRow("&Device:", self.dev)
        self.res = QComboBox()
        for r in (150, 200, 300, 400, 600):
            self.res.addItem(f"{r} dpi", r)
        self.res.setCurrentIndex(max(0, self.res.findData(settings()["scanner"].get("resolution", 300))))
        f.addRow("&Resolution:", self.res)
        self.mode = QComboBox()
        self.mode.addItems(["Color", "Gray", "Lineart"])
        self.mode.setCurrentText(settings()["scanner"].get("mode", "Color"))
        f.addRow("Color &mode:", self.mode)
        v.addLayout(f)
        self.lst = QListWidget()
        self.lst.setViewMode(QListWidget.IconMode)
        self.lst.setIconSize(QSize(110, 110))
        v.addWidget(self.lst, 1)
        h = QHBoxLayout()
        self.find = QPushButton("&Find Devices")
        self.scan_btn = QPushButton("&Scan Page")
        h.addWidget(self.find)
        h.addStretch()
        h.addWidget(self.scan_btn)
        v.addLayout(h)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("&Done")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.find.clicked.connect(self._find)
        self.scan_btn.clicked.connect(self._scan)

    def _find(self):
        def done(devs):
            cur = self.dev.currentData()
            self.dev.clear()
            self.dev.addItem("(default device)", "")
            for d, label in devs:
                self.dev.addItem(f"{label} [{d}]", d)
            i = self.dev.findData(cur)
            if i >= 0:
                self.dev.setCurrentIndex(i)
            if not devs:
                error(self, "No scanners were found.")
        run_task(self, "Searching for scanners...", lambda progress, cancel: scanner.list_devices(), on_done=done)

    def _scan(self):
        sc = settings()
        sc.data["scanner"] = {"device": self.dev.currentData() or "", "resolution": self.res.currentData(),
                              "mode": self.mode.currentText()}
        sc.save()

        def done(img):
            self.images.append(img)
            it = QListWidgetItem(thumbnail_icon(img, 110), f"Page {len(self.images)}")
            self.lst.addItem(it)
        run_task(self, "Scanning...", lambda progress, cancel: scanner.scan(
            self.dev.currentData() or "", self.res.currentData(), self.mode.currentText()), on_done=done)


def ask_page_range(parent, count, title="Pages to open"):
    from PySide6.QtWidgets import QInputDialog
    if count <= 1:
        return ""
    t, ok = QInputDialog.getText(parent, title, f"Sele&ct pages (e.g. 2,3,5-7)  — document has {count} pages.\n"
                                 "Leave empty for all pages:")
    if not ok:
        return None
    return t


def save_dialog(parent, fmt, base_name="Document", directory=None):
    label, ext, flt = exporters.FORMATS.get(fmt, (fmt.upper(), "." + fmt, exporters.IMAGE_FORMATS.get(fmt, "*")))
    start = str(Path(directory or Path.home()) / (Path(base_name).stem + ext))
    fn, _ = QFileDialog.getSaveFileName(parent, f"Save as {label}", start, flt + ";;All files (*)")
    if fn and not Path(fn).suffix:
        fn += ext
    return fn


def format_menu(menu, callback, include_pdf=True):
    """Fill a QMenu with the 'Save As' format list."""
    order = [("pdf", "&Searchable PDF Document"), ("pdf_image", "&Image-Only PDF Document"), None,
             ("docx", "Microsoft &Word Document"), ("xlsx", "Microsoft &Excel Workbook"),
             ("pptx", "Micr&osoft PowerPoint Presentation"), None, ("odt", "O&DT Document"),
             ("html", "&HTML Document"), ("epub", "EP&UB Document"), ("fb2", "FB&2 Document"),
             ("djvu", "D&jVu Document"), ("rtf", "R&TF Document"), ("txt", "T&XT Document"),
             ("csv", "&CSV Document")]
    for o in order:
        if o is None:
            menu.addSeparator()
            continue
        if not include_pdf and o[0].startswith("pdf"):
            continue
        a = menu.addAction(o[1])
        a.triggered.connect(lambda _=False, f=o[0]: callback(f))
    return menu
