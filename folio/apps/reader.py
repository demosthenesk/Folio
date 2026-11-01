"""Folio Reader — the 'New Task' start window and the PDF Editor."""
import datetime
import io
import json
import os
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QGuiApplication, QImage, QKeySequence,
                           QPainter, QPen)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QRadioButton,
                               QSpinBox, QStackedWidget, QTabWidget, QToolBar, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APPS
from ..core import document as D
from ..core import exporters, ocr, paths, pipeline, scanner
from ..core.ocr import build_table, words_to_lines
from ..core.settings import settings
from ..ui.dialogs import OptionsDialog, ScanDialog, format_menu, save_dialog
from ..ui.qtutil import (app_icon, ask, error, icon, info, make_app, pil_to_qimage, run_task, thumbnail_icon)
from ..ui.widgets import AboutDialog, LanguageCombo, MultiPageView, ThumbnailList
from . import pdfdialogs as PD

_windows = []

STAMPS = ["Approved", "As Is", "Confidential", "Departmental", "Experimental", "Expired", "Final", "For Comment",
          "For Public Release", "Not Approved", "Not For Public Release", "Sold", "Top Secret", "Draft"]


# ====================================================================== conversion dialog (start window tasks)
class ConvertDialog(QDialog):
    def __init__(self, fmt, files=None, parent=None, scan=False):
        super().__init__(parent)
        self.fmt = fmt
        label = {"pdf": "PDF", "docx": "Microsoft Word", "xlsx": "Microsoft Excel"}.get(fmt, "Other Formats")
        self.setWindowTitle(("Scan to " if scan else "Convert to ") + label)
        self.resize(640, 480)
        v = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        v.addWidget(QLabel("Files:"))
        v.addWidget(self.list, 1)
        h = QHBoxLayout()
        b_add = QPushButton(icon("document-open"), "&Add Files...")
        b_scan = QPushButton(icon("scan"), "&Scan...")
        b_rem = QPushButton("&Remove")
        b_add.clicked.connect(self.add)
        b_scan.clicked.connect(self.scan)
        b_rem.clicked.connect(lambda: [self.list.takeItem(self.list.row(i)) for i in self.list.selectedItems()])
        for b in (b_add, b_scan, b_rem):
            h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        g = QGroupBox("Settings")
        f = QFormLayout(g)
        self.fmt_box = QComboBox()
        for k, (lab, ext, _) in exporters.FORMATS.items():
            if fmt in ("pdf", "docx", "xlsx") and k != fmt:
                continue
            self.fmt_box.addItem(lab, k)
        if fmt not in ("pdf", "docx", "xlsx"):
            self.fmt_box.setCurrentIndex(max(0, self.fmt_box.findData(fmt if fmt in exporters.FORMATS else "odt")))
        f.addRow("Format:", self.fmt_box)
        self.lang = LanguageCombo()
        f.addRow("OCR languages:", self.lang)
        self.combine = QCheckBox("&Combine all files into one document")
        self.combine.setChecked(True)
        f.addRow(self.combine)
        self.searchable = QCheckBox("Make text in images &searchable (OCR)")
        self.searchable.setChecked(True)
        if fmt == "pdf":
            f.addRow(self.searchable)
        self.klb = QCheckBox("Keep &line breaks")
        self.klb.setChecked(settings()["keep_line_breaks"])
        self.kimg = QCheckBox("Keep &pictures")
        self.kimg.setChecked(settings()["export_images"])
        if fmt != "pdf":
            f.addRow(self.klb)
            f.addRow(self.kimg)
        self.open_after = QCheckBox("&Open document after saving")
        self.open_after.setChecked(True)
        f.addRow(self.open_after)
        v.addWidget(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Convert to " + label)
        bb.accepted.connect(self.run)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        for f_ in files or []:
            self._add_file(f_)
        if scan:
            QTimer.singleShot(0, self.scan)

    def _add_file(self, f):
        it = QListWidgetItem(icon("document-new"), Path(f).name)
        it.setData(Qt.UserRole, str(f))
        it.setToolTip(str(f))
        self.list.addItem(it)

    def add(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Select Files to Convert", str(Path.home()), D.OPEN_FILTER)
        for f in fns:
            self._add_file(f)

    def scan(self):
        dlg = ScanDialog(self)
        if dlg.exec() and dlg.images:
            tmp = Path(tempfile.mkdtemp(prefix="folio-scan-"))
            for i, img in enumerate(dlg.images):
                p = tmp / f"Scanned page {i + 1}.png"
                img.save(p, dpi=img.info.get("dpi", (300, 300)))
                self._add_file(p)

    def files(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def run(self):
        files = self.files()
        if not files:
            error(self, "No files to open.")
            return
        fmt = self.fmt_box.currentData()
        s = settings()
        if fmt != "pdf":
            s.data["keep_line_breaks"] = self.klb.isChecked()
            s.data["export_images"] = self.kimg.isChecked()
            s.save()
        langs = self.lang.languages()
        ext = exporters.FORMATS[fmt][1]
        if self.combine.isChecked() or len(files) == 1:
            out = save_dialog(self, fmt, Path(files[0]).stem)
            if not out:
                return
            jobs = [(files, out)]
        else:
            d = QFileDialog.getExistingDirectory(self, "Save to folder", str(Path(files[0]).parent))
            if not d:
                return
            jobs = [([f], str(Path(d) / (Path(f).stem + ext))) for f in files]
        searchable = self.searchable.isChecked()

        def work(progress, cancel):
            outs = []
            for fl, out in jobs:
                if fmt == "pdf":
                    outs += pipeline.make_pdf(fl, out, langs, recognize=searchable, progress=progress, cancel=cancel)
                else:
                    outs += pipeline.convert(fl, fmt, out, langs, progress=progress, cancel=cancel)
            return outs

        def done(outs):
            for f in files:
                settings().add_recent("recent_documents", f)
            if self.open_after.isChecked() and outs:
                if fmt == "pdf":
                    PdfEditor.open_window(outs[0])
                else:
                    paths.open_with_default(outs[0])
            self.accept()
        run_task(self, "Converting...", work, on_done=done)


# ====================================================================== start window
class TaskTile(QToolButton):
    def __init__(self, ic, title, desc, fn):
        super().__init__()
        self.setIcon(ic)
        self.setIconSize(QSize(40, 40))
        self.setText(f"{title}\n{desc}" if desc else title)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setMinimumSize(300, 84)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("QToolButton{background:white;border:1px solid #d6dbe1;border-radius:8px;padding:8px;"
                           "text-align:left;font-size:10.5pt} QToolButton:hover{border-color:#e8543f;background:#fff7f5}")
        self.clicked.connect(fn)


class StartWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"New Task — {APPS['reader'][0]}")
        self.setWindowIcon(app_icon("reader"))
        self.resize(1080, 700)
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        nav = QFrame()
        nav.setFixedWidth(200)
        nav.setStyleSheet("QFrame{background:#2d3a4a} QPushButton{color:#dfe6ee;background:transparent;border:none;"
                          "text-align:left;padding:12px 18px;font-size:11pt} QPushButton:checked{background:#e8543f;color:white}"
                          "QPushButton:hover:!checked{background:#3b4a5c}")
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(0, 12, 0, 12)
        logo = QLabel()
        logo.setPixmap(app_icon("reader").pixmap(48, 48))
        logo.setAlignment(Qt.AlignCenter)
        nv.addWidget(logo)
        title = QLabel("Folio")
        title.setStyleSheet("color:white;font-size:16pt;font-weight:bold")
        title.setAlignment(Qt.AlignCenter)
        nv.addWidget(title)
        nv.addSpacing(16)
        self.stack = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        for i, name in enumerate(["Open", "Scan", "Compare", "Recent"]):
            b = QPushButton(name)
            b.setCheckable(True)
            self.nav_group.addButton(b, i)
            nv.addWidget(b)
        nv.addStretch()
        self.nav_group.idClicked.connect(self._nav)
        h.addWidget(nav)
        right = QWidget()
        right.setStyleSheet("background:#f3f5f7")
        rv = QVBoxLayout(right)
        rv.addWidget(self.stack, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("OCR languages:"))
        self.lang = LanguageCombo()
        bottom.addWidget(self.lang)
        bottom.addStretch()
        ob = QPushButton("Options...")
        ob.clicked.connect(lambda: OptionsDialog(self).exec())
        bottom.addWidget(ob)
        rv.addLayout(bottom)
        h.addWidget(right, 1)
        self.setCentralWidget(root)
        self._pages()
        self._menus()
        self.nav_group.button(0).setChecked(True)
        self.setAcceptDrops(True)

    def _page(self, heading, tiles):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 18, 24, 6)
        lab = QLabel(heading)
        lab.setStyleSheet("font-size:15pt;font-weight:bold;color:#2d3a4a")
        v.addWidget(lab)
        g = QGridLayout()
        g.setSpacing(12)
        for i, t in enumerate(tiles):
            g.addWidget(t, i // 2, i % 2)
        v.addLayout(g)
        v.addStretch()
        return w

    def _pages(self):
        R = icon("pdf-editor")
        self.stack.addWidget(self._page("Open", [
            TaskTile(R, "Open PDF Document", "View, edit, comment on and protect PDF files", self.open_pdf),
            TaskTile(icon("ocr-editor"), "Open in OCR Editor", "Recognize scans and photos, verify text",
                     self.open_in_ocr),
            TaskTile(R, "Convert to PDF", "Searchable PDFs from scans and documents",
                     lambda: ConvertDialog("pdf", parent=self).exec()),
            TaskTile(icon("doc-word"), "Convert to Microsoft Word",
                     "Editable DOCX from PDF and image files", lambda: ConvertDialog("docx", parent=self).exec()),
            TaskTile(icon("doc-excel"), "Convert to Microsoft Excel",
                     "Spreadsheets from PDF and image files", lambda: ConvertDialog("xlsx", parent=self).exec()),
            TaskTile(icon("doc-other"), "Convert to Other Formats",
                     "ODT, RTF, PPTX, HTML, EPUB, TXT...", lambda: ConvertDialog("odt", parent=self).exec()),
            TaskTile(icon("page-add"), "New PDF Document", "Combine files into a new PDF",
                     self.new_pdf),
        ]))
        sc = [
            TaskTile(icon("ocr-editor"), "Scan to OCR Editor", "Scan pages and recognize them in the OCR Editor", self.scan_to_ocr),
            TaskTile(icon("scan"), "Scan to PDF", "Create a searchable PDF from paper documents",
                     lambda: ConvertDialog("pdf", parent=self, scan=True).exec()),
            TaskTile(icon("doc-word"), "Scan to Microsoft Word", "Create an editable document",
                     lambda: ConvertDialog("docx", parent=self, scan=True).exec()),
            TaskTile(icon("doc-excel"), "Scan to Microsoft Excel", "Create a spreadsheet",
                     lambda: ConvertDialog("xlsx", parent=self, scan=True).exec()),
            TaskTile(icon("scan"), "Scan to Image File", "Save scans as PNG, JPEG or TIFF", self.scan_to_image),
            TaskTile(icon("scan"), "Scan to Other Formats", "ODT, RTF, HTML, EPUB, TXT...",
                     lambda: ConvertDialog("odt", parent=self, scan=True).exec()),
        ]
        page = self._page("Scan", sc)
        if not scanner.available():
            n = QLabel("Scanning uses SANE. Install it with: <b>sudo apt install sane-utils</b>")
            page.layout().insertWidget(1, n)
        self.stack.addWidget(page)
        cmp_tile = TaskTile(icon("compare"), "Open Compare Documents",
                            "Find differences between two versions",
                            lambda: paths.launch_sibling("compare"))
        page = self._page("Compare", [cmp_tile])
        self.recent_cmp = QListWidget()
        self.recent_cmp.itemDoubleClicked.connect(lambda it: paths.launch_sibling("compare", *it.data(Qt.UserRole).split("|")))
        page.layout().insertWidget(2, QLabel("Recent Comparisons:"))
        page.layout().insertWidget(3, self.recent_cmp, 1)
        self.stack.addWidget(page)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 18, 24, 6)
        lab = QLabel("Recent PDF Documents and OCR Projects")
        lab.setStyleSheet("font-size:15pt;font-weight:bold;color:#2d3a4a")
        v.addWidget(lab)
        self.flt = QLineEdit(placeholderText="Find Recent Documents")
        v.addWidget(self.flt)
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(lambda it: self.open_any(it.data(Qt.UserRole)))
        v.addWidget(self.recent, 1)
        self.flt.textChanged.connect(self._fill_recent)
        self.stack.addWidget(w)

    def _nav(self, i):
        self.stack.setCurrentIndex(i)
        if i == 3:
            self._fill_recent()
        if i == 2:
            self.recent_cmp.clear()
            for r in settings()["recent_comparisons"]:
                a, b = r.split("|", 1)
                it = QListWidgetItem(f"{Path(a).name}  ↔  {Path(b).name}")
                it.setData(Qt.UserRole, r)
                self.recent_cmp.addItem(it)

    def _fill_recent(self):
        self.recent.clear()
        t = self.flt.text().lower()
        for r in settings()["recent_documents"]:
            if Path(r).exists() and t in r.lower():
                it = QListWidgetItem(icon("ocr-editor" if r.endswith(D.PROJECT_EXT) else "pdf-editor"),
                                     f"{Path(r).name}\n{r}")
                it.setData(Qt.UserRole, r)
                self.recent.addItem(it)

    def _menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("New &PDF Document", self.new_pdf)
        f.addAction("&Open PDF Document...", self.open_pdf, QKeySequence.Open)
        f.addSeparator()
        cv = f.addMenu("Con&vert To")
        cv.addAction("&PDF Document", lambda: ConvertDialog("pdf", parent=self).exec())
        cv.addAction("Microsoft &Word", lambda: ConvertDialog("docx", parent=self).exec())
        cv.addAction("Microsoft E&xcel", lambda: ConvertDialog("xlsx", parent=self).exec())
        cv.addAction("&Other Formats", lambda: ConvertDialog("odt", parent=self).exec())
        st = f.addMenu("Scan &To")
        st.addAction("&PDF Document", lambda: ConvertDialog("pdf", parent=self, scan=True).exec())
        st.addAction("Microsoft &Word", lambda: ConvertDialog("docx", parent=self, scan=True).exec())
        st.addAction("Microsoft E&xcel", lambda: ConvertDialog("xlsx", parent=self, scan=True).exec())
        st.addAction("I&mage File", self.scan_to_image)
        st.addAction("&Other Formats", lambda: ConvertDialog("odt", parent=self, scan=True).exec())
        f.addSeparator()
        f.addAction("Open in OC&R Editor...", self.open_in_ocr)
        f.addAction("Sc&an to OCR Editor", self.scan_to_ocr)
        f.addSeparator()
        f.addAction("Ne&w OCR Project", lambda: paths.launch_sibling("ocr"))
        f.addAction("Open OCR Pro&ject...", self.open_project)
        f.addSeparator()
        f.addAction("E&xit", self.close, QKeySequence.Quit)
        v = mb.addMenu("&View")
        for i, n in enumerate(["&Open", "&Scan", "&Compare", "&Recent"]):
            v.addAction(n, lambda i=i: (self.nav_group.button(i).setChecked(True), self._nav(i)), f"Ctrl+{i + 1}")
        t = mb.addMenu("&Tools")
        t.addAction(icon("ocr-editor"), "OCR Editor", lambda: paths.launch_sibling("ocr"))
        t.addAction(icon("compare"), "Compare Documents", lambda: paths.launch_sibling("compare"))
        t.addAction(icon("capture"), "Folio Snap", lambda: paths.launch_sibling("snap"))
        t.addAction("Hot Folder...", self.hot_folder)
        t.addSeparator()
        t.addAction("&Options...", lambda: OptionsDialog(self).exec())
        h = mb.addMenu("&Help")
        h.addAction("Ab&out", lambda: AboutDialog("reader", self).exec())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.open_any(u.toLocalFile())

    def open_any(self, f):
        if f.endswith(D.PROJECT_EXT):
            paths.launch_sibling("ocr", f)
        elif D.kind_of(f) == "pdf" and f.lower().endswith(".pdf"):
            PdfEditor.open_window(f)
        else:
            PdfEditor.open_window(f)

    def open_pdf(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open PDF Document", str(Path.home()),
                                            "PDF files (*.pdf);;" + D.OPEN_FILTER)
        if fn:
            PdfEditor.open_window(fn)

    def new_pdf(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Select files for the new PDF document", str(Path.home()), D.OPEN_FILTER)
        if not fns:
            PdfEditor.open_window(None)
            return
        out = str(Path(tempfile.mkdtemp(prefix="folio-new-")) / "New Document.pdf")
        langs = self.lang.languages()
        run_task(self, "Creating document...", lambda progress, cancel: pipeline.make_pdf(fns, out, langs, True,
                 progress=progress, cancel=cancel), on_done=lambda o: PdfEditor.open_window(o[0], untitled=True))

    def open_in_ocr(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Open in OCR Editor", str(Path.home()), D.OPEN_FILTER)
        if fns:
            paths.launch_sibling("ocr", *fns)

    def open_project(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open OCR Project", str(Path.home()), f"Folio OCR projects (*{D.PROJECT_EXT})")
        if fn:
            paths.launch_sibling("ocr", fn)

    def scan_to_ocr(self):
        dlg = ScanDialog(self)
        if dlg.exec() and dlg.images:
            tmp = Path(tempfile.mkdtemp(prefix="folio-scan-"))
            files = []
            for i, img in enumerate(dlg.images):
                p = tmp / f"Scan {i + 1:03d}.png"
                img.save(p, dpi=img.info.get("dpi", (300, 300)))
                files.append(p)
            paths.launch_sibling("ocr", *files)

    def scan_to_image(self):
        dlg = ScanDialog(self)
        if dlg.exec() and dlg.images:
            fn, _ = QFileDialog.getSaveFileName(self, "Save Images", str(Path.home() / "Scan.png"),
                                                "PNG (*.png);;JPEG (*.jpg);;Multi-page TIFF (*.tif)")
            if fn:
                ext = (Path(fn).suffix.lower().lstrip(".") or "png").replace("jpeg", "jpg").replace("tiff", "tif")
                exporters.export_images([exporters.ExportPage(i) for i in dlg.images], fn, ext)

    def hot_folder(self):
        from .ocreditor import HotFolderDialog
        HotFolderDialog(self).exec()


# ====================================================================== PDF editor
def to_unrot(page, r):
    return (r * page.derotation_matrix) if page.rotation else r


def to_disp(page, r):
    return (r * page.rotation_matrix) if page.rotation else r


class PdfEditor(QMainWindow):
    @staticmethod
    def open_window(path, untitled=False):
        w = PdfEditor()
        if path:
            if not w.load(path, untitled=untitled):
                w.deleteLater()
                return None
        w.show()
        _windows.append(w)
        return w

    def __init__(self):
        super().__init__()
        self.setWindowIcon(app_icon("reader"))
        self.resize(1400, 900)
        self.doc = None
        self.path = None
        self.untitled = True
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.encryption = None
        self.tool = "select"
        self.history, self.hist_pos = [], -1
        self.selection = None  # (page, display rect)
        self.stamp = ("builtin", 0)
        self.signature = None
        self.render_cache = {}
        self._build_ui()
        self._build_menus()
        self._update_title()
        self._update_enabled()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        tb = QToolBar("Main")
        tb.setObjectName("main")
        tb.setIconSize(QSize(22, 22))
        self.addToolBar(tb)
        tb.addAction(icon("pdf-editor"), "New Task", self.new_task)
        tb.addAction(icon("document-open"), "Open", self.open_dialog)
        tb.addAction(icon("document-save"), "Save", self.save)
        tb.addAction(icon("print"), "Print", self.print_doc)
        tb.addSeparator()
        tb.addAction(icon("undo"), "Undo", self.undo)
        tb.addAction(icon("redo"), "Redo", self.redo)
        tb.addSeparator()
        self.tool_group = QActionGroup(self)
        self.tool_acts = {}

        def tool(key, ic, tip, bar):
            a = bar.addAction(icon(ic), tip)
            a.setCheckable(True)
            a.setActionGroup(self.tool_group)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_acts[key] = a
            return a
        tool("select", "tool-select", "Select Objects", tb).setChecked(True)
        tool("hand", "tool-hand", "Hand Tool", tb)
        tb.addSeparator()
        tb.addAction(icon("zoom-out"), "Zoom Out", lambda: self.view.zoom_out())
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        for z in ("50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%", "Fit Width", "Best Fit"):
            self.zoom_box.addItem(z)
        self.zoom_box.setMinimumWidth(95)
        self.zoom_box.activated.connect(self._zoom_box)
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self._zoom_box(-1))
        tb.addWidget(self.zoom_box)
        tb.addAction(icon("zoom-in"), "Zoom In", lambda: self.view.zoom_in())
        tb.addSeparator()
        tb.addAction(icon("go-up"), "Previous Page", lambda: self.goto(self.view.cur_page - 1))
        self.page_spin = QSpinBox(minimum=1, maximum=1)
        self.page_spin.editingFinished.connect(lambda: self.goto(self.page_spin.value() - 1))
        tb.addWidget(self.page_spin)
        self.page_count = QLabel(" / 0 ")
        tb.addWidget(self.page_count)
        tb.addAction(icon("go-down"), "Next Page", lambda: self.goto(self.view.cur_page + 1))
        tb.addSeparator()
        tb.addAction(icon("recognize"), "Recognize", self.recognize)
        tb.addAction(icon("ocr-editor"), "OCR Editor", self.to_ocr_editor)
        tb.addAction(icon("compare"), "Compare", self.compare)
        tb.addSeparator()
        self.saveas_btn = QToolButton(text="Save As", icon=icon("send"))
        self.saveas_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.saveas_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(self)
        m.addAction("PDF Doc&ument...", self.save_as_pdf)
        m.addSeparator()
        format_menu(m, self.save_as_format)
        m.addSeparator()
        m.addAction("P&age Images...", self.save_page_images)
        self.saveas_btn.setMenu(m)
        tb.addWidget(self.saveas_btn)

        # PDF tools bar
        self.tools_bar = QToolBar("PDF Tools")
        self.tools_bar.setObjectName("tools")
        self.tools_bar.setIconSize(QSize(22, 22))
        self.addToolBarBreak()
        self.addToolBar(self.tools_bar)
        t = self.tools_bar
        tool("note", "note", "Add Note", t)
        tool("highlight", "highlight", "Highlight", t)
        tool("underline", "underline", "Underline", t)
        tool("strike", "strikethrough", "Strikethrough", t)
        tool("caret", "edit-text", "Insert Text", t)
        t.addSeparator()
        tool("ink", "ink", "Ink", t)
        tool("line", "line", "Line", t)
        tool("arrow", "arrow", "Arrow", t)
        tool("rect", "rect", "Rectangle", t)
        tool("oval", "oval", "Oval", t)
        tool("textbox", "textbox", "Text Box", t)
        t.addSeparator()
        self.stamp_btn = QToolButton(icon=icon("stamp"), toolTip="Stamp")
        self.stamp_btn.setPopupMode(QToolButton.MenuButtonPopup)
        sm = QMenu(self)
        for i, n in enumerate(STAMPS):
            sm.addAction(n, lambda i=i: self._choose_stamp(("builtin", i)))
        sm.addSeparator()
        sm.addAction("Create Te&xt Stamp...", self.create_text_stamp)
        sm.addAction("&Create Stamp from Image...", self.create_image_stamp)
        self.stamp_btn.setMenu(sm)
        self.stamp_btn.clicked.connect(lambda: self.set_tool("stamp"))
        t.addWidget(self.stamp_btn)
        self.sig_btn = QToolButton(icon=icon("signature"), toolTip="Signature")
        self.sig_btn.setPopupMode(QToolButton.MenuButtonPopup)
        gm = QMenu(self)
        gm.addAction("&Create Signature from Image...", self.create_image_signature)
        gm.addAction("Create &Text Signature...", self.create_text_signature)
        self.sig_btn.setMenu(gm)
        self.sig_btn.clicked.connect(self._signature_tool)
        t.addWidget(self.sig_btn)
        t.addSeparator()
        tool("edittext", "edit-text", "Edit Text", t)
        tool("picture", "picture-add", "Add Picture", t)
        tool("link", "link", "Add Link", t)
        tool("erase", "erase", "Erase Text and Pictures", t)
        tool("crop", "crop", "Crop Pages", t)
        t.addSeparator()
        t.addAction(icon("header-footer"), "Header and Footer", self.header_footer)
        t.addAction(icon("watermark"), "Add Watermark", self.watermark)
        t.addSeparator()
        tool("redact", "redact", "Redaction Mode", t)
        t.addAction("Apply Redactions", self.apply_redactions)
        t.addAction(icon("password"), "Password Security", self.security)

        # view
        self.view = MultiPageView()
        self.view.unit_scale = 96.0 / 72.0
        self.view.pageChanged.connect(self._page_changed)
        self.view.rectDrawn.connect(self._rect)
        self.view.clicked.connect(self._click)
        self.view.doubleClicked.connect(self._dclick)
        self.view.inkDrawn.connect(self._ink)
        self.view.lineDrawn.connect(self._line)
        self.view.contextRequested.connect(self._context)
        self.view.zoomChanged.connect(lambda z: self.zoom_box.setEditText(f"{round(z * 100)}%"))
        self.setCentralWidget(self.view)
        self.setAcceptDrops(True)

        # navigation pane
        self.nav = QTabWidget()
        self.nav.setTabPosition(QTabWidget.West)
        self.thumbs = ThumbnailList(size=110)
        self.thumbs.currentRowChanged.connect(lambda r: self.goto(r) if r >= 0 and r != self.view.cur_page else None)
        self.thumbs.moved.connect(self._pages_moved)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        self.nav.addTab(self.thumbs, icon("pages"), "")
        self.nav.setTabToolTip(0, "Pages")
        self.bm = QTreeWidget()
        self.bm.setHeaderHidden(True)
        self.bm.itemClicked.connect(lambda it, _: self.goto(it.data(0, Qt.UserRole)))
        self.bm.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bm.customContextMenuRequested.connect(self._bm_menu)
        self.nav.addTab(self.bm, icon("bookmark"), "")
        self.nav.setTabToolTip(1, "Bookmarks")
        sw = QWidget()
        sv = QVBoxLayout(sw)
        sv.setContentsMargins(4, 4, 4, 4)
        self.search_edit = QLineEdit(placeholderText="Search")
        self.search_edit.returnPressed.connect(self.search)
        sv.addWidget(self.search_edit)
        self.search_case = QCheckBox("Match case")
        sv.addWidget(self.search_case)
        self.search_res = QListWidget()
        self.search_res.setWordWrap(True)
        self.search_res.itemClicked.connect(self._search_go)
        sv.addWidget(self.search_res, 1)
        h = QHBoxLayout()
        for t_, kind in (("Highlight", "highlight"), ("Underline", "underline"), ("Strike", "strike"), ("Redact", "redact")):
            b = QPushButton(t_)
            b.clicked.connect(lambda _=False, k=kind: self.markup_results(k))
            h.addWidget(b)
        sv.addLayout(h)
        self.nav.addTab(sw, icon("search"), "")
        self.nav.setTabToolTip(2, "Search")
        aw = QWidget()
        av = QVBoxLayout(aw)
        av.setContentsMargins(4, 4, 4, 4)
        self.att = QListWidget()
        self.att.itemDoubleClicked.connect(lambda it: self.save_attachment(it.text()))
        av.addWidget(self.att, 1)
        h = QHBoxLayout()
        for t_, fn in (("Add...", self.add_attachment), ("Save...", lambda: self.save_attachment()),
                       ("Delete", self.delete_attachment)):
            b = QPushButton(t_)
            b.clicked.connect(fn)
            h.addWidget(b)
        av.addLayout(h)
        self.nav.addTab(aw, icon("attachment"), "")
        self.nav.setTabToolTip(3, "Attachments")
        self.nav_dock = QDockWidget("Navigation", self)
        self.nav_dock.setObjectName("nav")
        self.nav_dock.setWidget(self.nav)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.nav_dock)
        self.resizeDocks([self.nav_dock], [190], Qt.Horizontal)

        # comments pane
        self.comments = QListWidget()
        self.comments.setWordWrap(True)
        self.comments.itemClicked.connect(self._comment_go)
        self.comments.itemDoubleClicked.connect(self._comment_edit)
        self.comments.setContextMenuPolicy(Qt.CustomContextMenu)
        self.comments.customContextMenuRequested.connect(self._comment_menu)
        self.comments_dock = QDockWidget("Comments", self)
        self.comments_dock.setObjectName("comments")
        self.comments_dock.setWidget(self.comments)
        self.addDockWidget(Qt.RightDockWidgetArea, self.comments_dock)
        self.comments_dock.hide()

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("Ne&w Task...", self.new_task, "Ctrl+N")
        f.addAction(icon("document-open"), "&Open...", self.open_dialog, QKeySequence.Open)
        f.addSeparator()
        f.addAction(icon("document-save"), "&Save", self.save, QKeySequence.Save)
        sa = f.addMenu("Sa&ve As")
        sa.addAction("PDF Doc&ument...", self.save_as_pdf, "Ctrl+Shift+S")
        sa.addSeparator()
        format_menu(sa, self.save_as_format)
        sa.addSeparator()
        sa.addAction("P&age Images...", self.save_page_images)
        f.addAction("&Close Document", self.close, "Ctrl+W")
        f.addSeparator()
        f.addAction(icon("recognize"), "Recogni&ze Document", self.recognize)
        f.addAction(icon("compare"), "Co&mpare Documents", self.compare)
        f.addSeparator()
        f.addAction(icon("password"), "&Password Security...", self.security)
        f.addAction("Red&uce File Size...", self.reduce_size)
        f.addSeparator()
        f.addAction("&E-mail...", self.email)
        f.addAction(icon("print"), "Pr&int...", self.print_doc, QKeySequence.Print)
        f.addSeparator()
        f.addAction("&Document Properties...", self.properties, "Ctrl+D")
        f.addSeparator()
        self.recent_menu = f.addMenu("Recent &Files")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        f.addAction("E&xit", QApplication.quit, QKeySequence.Quit)

        e = mb.addMenu("&Edit")
        e.addAction(icon("undo"), "&Undo", self.undo, QKeySequence.Undo)
        e.addAction(icon("redo"), "&Redo", self.redo, QKeySequence.Redo)
        e.addSeparator()
        e.addAction("C&opy", self.copy_text, QKeySequence.Copy)
        ca = e.addMenu("Cop&y As")
        ca.addAction("I&mage", self.copy_image)
        ca.addAction("&Text", self.copy_text)
        ca.addAction("T&able", self.copy_table)
        e.addAction("&Delete", self.delete_selected_annot, QKeySequence.Delete)
        e.addSeparator()
        e.addAction("Select &All", self.select_all, QKeySequence.SelectAll)
        e.addAction("Cance&l Selection", self.clear_selection, "Esc")
        e.addSeparator()
        ap = e.addMenu("Add Pages")
        ap.addAction("&Add from File...", self.add_pages_from_file)
        ap.addAction("Add from &Scanner...", self.add_pages_from_scanner)
        ap.addAction("Add Blank &Page", self.add_blank_page)
        rp = e.addMenu("Ro&tate Pages")
        rp.addAction(icon("rotate-right"), "&Rotate Right", lambda: self.rotate_pages(90), "Ctrl+Shift+Right")
        rp.addAction(icon("rotate-left"), "R&otate Left", lambda: self.rotate_pages(-90), "Ctrl+Shift+Left")
        rp.addSeparator()
        rp.addAction("Correct Orientation of All P&ages", self.correct_orientation)
        e.addAction(icon("crop"), "Crop Pa&ges...", lambda: self.set_tool("crop"))
        e.addAction("En&hance Page Images...", self.enhance_images)
        e.addAction("R&eplace Pages...", self.replace_pages)
        e.addAction(icon("page-delete"), "Delete Page&s...", self.delete_pages)
        e.addAction("E&xtract Pages...", self.extract_pages)
        e.addSeparator()
        e.addAction(icon("picture-add"), "Add P&icture...", lambda: self.set_tool("picture"))
        e.addAction(icon("bookmark"), "Add Book&mark...", self.add_bookmark, "Ctrl+B")
        e.addAction(icon("link"), "Add Link&...", lambda: self.set_tool("link"))
        e.addAction(icon("attachment"), "Add& Attachment...", self.add_attachment)
        e.addSeparator()
        e.addAction(icon("search"), "&Find...", self.find, QKeySequence.Find)
        e.addAction("Find Ne&xt", self.find_next, QKeySequence.FindNext)
        e.addSeparator()
        fd = e.addMenu("Form Data")
        fd.addAction("&Load Form Data from File...", self.load_form)
        fd.addAction("&Save Form Data to File...", self.save_form)
        fd.addSeparator()
        fd.addAction("Reset &Form", self.reset_form)
        e.addSeparator()
        e.addAction("Delete Ob&jects and Data...", self.delete_data)
        e.addAction("Delete Comments and Mar&kup...", self.delete_comments)

        v = mb.addMenu("&View")
        v.addAction(self.nav_dock.toggleViewAction())
        v.addSeparator()
        for i, n in enumerate(["&Pages", "Boo&kmarks", "&Search", "&Attachments"]):
            v.addAction(n, lambda i=i: (self.nav_dock.show(), self.nav.setCurrentIndex(i)))
        v.addSeparator()
        a = v.addAction("Sh&ow PDF Tools")
        a.setCheckable(True)
        a.setChecked(True)
        a.toggled.connect(self.tools_bar.setVisible)
        v.addAction(self.comments_dock.toggleViewAction())
        v.addSeparator()
        v.addAction(icon("zoom-in"), "&Zoom In", lambda: self.view.zoom_in(), QKeySequence.ZoomIn)
        v.addAction(icon("zoom-out"), "Zoom O&ut", lambda: self.view.zoom_out(), QKeySequence.ZoomOut)
        v.addAction(icon("fit-page"), "Best F&it", lambda: self.view.fit_page(), "Ctrl+0")
        v.addAction("A&ctual Size", lambda: self.view.set_zoom(1.0), "Ctrl+8")
        v.addAction(icon("fit-width"), "&Fit to Width", lambda: self.view.fit_width(), "Ctrl+9")
        v.addSeparator()
        gp = v.addMenu("&Go to Page")
        gp.addAction("&Page Number...", self.goto_dialog, "Ctrl+G")
        gp.addSeparator()
        gp.addAction("Ne&xt Page", lambda: self.goto(self.view.cur_page + 1), "Ctrl+Down")
        gp.addAction("P&revious Page", lambda: self.goto(self.view.cur_page - 1), "Ctrl+Up")
        gp.addAction("&First Page", lambda: self.goto(0), "Ctrl+Home")
        gp.addAction("&Last Page", lambda: self.goto(len(self.doc) - 1 if self.doc else 0), "Ctrl+End")
        gp.addSeparator()
        gp.addAction("B&ack", self.back, "Alt+Left")
        gp.addAction("Forwar&d", self.forward, "Alt+Right")
        vm = v.addMenu("View &Mode")
        grp = QActionGroup(self)
        for t_, mode in (("One-Page &Scrolling", "continuous"), ("T&wo-Page Scrolling", "two")):
            a = vm.addAction(t_, lambda m=mode: self.view.set_layout(m))
            a.setCheckable(True)
            a.setActionGroup(grp)
            a.setChecked(mode == "continuous")
        v.addAction("Full Sc&reen", self.toggle_full, "F11")
        v.addSeparator()
        a = v.addAction("Highlight Fiel&ds")
        a.setCheckable(True)
        a.toggled.connect(self.highlight_fields)

        t = mb.addMenu("&Tools")
        t.addAction(icon("tool-select"), "Select Ob&jects", lambda: self.set_tool("select"))
        t.addAction(icon("tool-hand"), "&Hand Tool", lambda: self.set_tool("hand"))
        t.addSeparator()
        cm = t.addMenu("Comments and Mark&up")
        for k, n in (("note", "&Add Note"), ("highlight", "&Highlight"), ("underline", "&Underline"),
                     ("strike", "S&trikethrough"), ("caret", "Insert Te&xt")):
            cm.addAction(n, lambda k=k: self.set_tool(k))
        cm.addSeparator()
        cm.addAction("&Search and Mark Up", lambda: (self.nav_dock.show(), self.nav.setCurrentIndex(2), self.search_edit.setFocus()))
        dm = t.addMenu("Dra&wing Tools")
        for k, n in (("ink", "&Ink"), ("line", "&Line"), ("arrow", "&Arrow"), ("rect", "Rectan&gle"),
                     ("oval", "O&val"), ("textbox", "T&ext Box")):
            dm.addAction(n, lambda k=k: self.set_tool(k))
        t.addSeparator()
        t.addAction(icon("edit-text"), "&Edit Text and Pictures", lambda: self.set_tool("edittext"))
        t.addAction(icon("erase"), "E&rase Text and Pictures", lambda: self.set_tool("erase"))
        t.addSeparator()
        st = t.addMenu("Sta&mp")
        st.addAction("&Stamp", lambda: self.set_tool("stamp"))
        st.addSeparator()
        st.addAction("&Create Stamp from Image...", self.create_image_stamp)
        st.addAction("C&reate Text Stamp...", self.create_text_stamp)
        wm = t.addMenu("Watermar&k")
        wm.addAction("&Add Watermark...", self.watermark)
        wm.addAction("&Delete Watermarks", lambda: self.remove_tracked("FolioWM", "watermarks"))
        hf = t.addMenu("He&ader and Footer")
        hf.addAction("&Create Header and Footer...", self.header_footer)
        hf.addAction("&Delete Headers and Footers", lambda: self.remove_tracked("FolioHF", "headers and footers"))
        sg = t.addMenu("&Signature")
        sg.addAction("&Signature", self._signature_tool)
        sg.addAction("&Create Signature from Image...", self.create_image_signature)
        sg.addAction("Create &Text Signature...", self.create_text_signature)
        t.addAction(icon("redact"), "Re&daction Mode", lambda: self.set_tool("redact"))
        t.addAction("Apply Redactions", self.apply_redactions)
        t.addAction(icon("password"), "Password Securit&y...", self.security)
        t.addSeparator()
        t.addAction(icon("ocr-editor"), "OCR Ed&itor", self.to_ocr_editor)
        t.addAction(icon("compare"), "&Compare Documents", self.compare)
        t.addAction("Hot &Folder...", lambda: __import__("folio.apps.ocreditor", fromlist=["x"]).HotFolderDialog(self).exec())
        t.addAction(icon("capture"), "Folio Sna&p", lambda: paths.launch_sibling("snap"))
        t.addSeparator()
        t.addAction("Options&...", lambda: OptionsDialog(self).exec())
        h = mb.addMenu("&Help")
        h.addAction("&Quick Start Guide", self.quick_help, "F1")
        h.addAction("Ab&out", lambda: AboutDialog("reader", self).exec())

    # ------------------------------------------------------------------ document lifecycle
    def _update_title(self):
        name = Path(self.path).name if self.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.dirty else ''} — {APPS['reader'][0]} PDF Editor")

    def _update_enabled(self):
        has = self.doc is not None and len(self.doc) > 0
        self.tools_bar.setEnabled(has)

    def load(self, path, untitled=False, password=None):
        try:
            k = D.kind_of(path)
            src = path
            if k != "pdf" or not path.lower().endswith(".pdf"):
                # images / office files are converted to a PDF first
                out = str(Path(tempfile.mkdtemp(prefix="folio-open-")) / (Path(path).stem + ".pdf"))
                if k == "image":
                    pipeline.make_pdf([path], out, recognize=False)
                else:
                    doc = pymupdf.open(D.as_pdf_path(path))
                    doc.save(out) if doc.is_pdf else pymupdf.open("pdf", doc.convert_to_pdf()).save(out)
                src, untitled = out, True
            doc = pymupdf.open(src)
            if doc.needs_pass:
                while True:
                    pw, ok = QInputDialog.getText(self, "Password Required",
                                                  f"Enter the password to open\n{Path(path).name}:", QLineEdit.Password)
                    if not ok:
                        return False
                    if doc.authenticate(pw):
                        self.encryption = {"user_pw": pw, "owner_pw": pw, "keep": True}
                        break
        except Exception as e:
            error(self, f"Cannot open the document {Path(path).name}.\n{e}")
            return False
        self.doc = doc
        self.path = src
        self.untitled = untitled
        self.dirty = False
        if not untitled:
            settings().add_recent("recent_documents", path)
        self.refresh_all(reset_view=True)
        QTimer.singleShot(50, self.view.fit_width)
        return True

    def refresh_all(self, reset_view=False, pages=None):
        doc = self.doc
        self.render_cache.clear()
        sizes = [(p.rect.width, p.rect.height) for p in doc] if doc else []
        self.view.set_document(sizes, self._render, unit_scale=96.0 / 72.0, keep_position=not reset_view)
        self.page_spin.setMaximum(max(1, len(sizes)))
        self.page_count.setText(f" / {len(sizes)} ")
        self.refresh_thumbs()
        self.refresh_bookmarks()
        self.refresh_attachments()
        self.refresh_comments()
        self.highlight_fields(getattr(self, "_fields_on", False))
        self._update_title()
        self._update_enabled()

    def _render(self, i, scale):
        page = self.doc[i]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
        return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()

    def refresh_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        if self.doc:
            for i, p in enumerate(self.doc):
                z = 110 / max(p.rect.width, p.rect.height)
                pix = p.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
                q = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                from PySide6.QtGui import QIcon, QPixmap
                it = QListWidgetItem(QIcon(QPixmap.fromImage(q)), str(i + 1))
                it.setData(Qt.UserRole, i)
                self.thumbs.addItem(it)
        self.thumbs.blockSignals(False)

    def refresh_pages(self, pages=None):
        """Re-render after content changes (keeps scroll position)."""
        self.view.invalidate(pages)
        if pages is not None:
            for i in pages:
                if 0 <= i < self.thumbs.count():
                    p = self.doc[i]
                    z = 110 / max(p.rect.width, p.rect.height)
                    pix = p.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
                    q = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                    from PySide6.QtGui import QIcon, QPixmap
                    self.thumbs.item(i).setIcon(QIcon(QPixmap.fromImage(q)))
        self.refresh_comments()

    def mark_dirty(self):
        self.dirty = True
        self._update_title()

    def push_undo(self):
        if not self.doc:
            return
        self.undo_stack.append(self.doc.tobytes())
        self.undo_stack = self.undo_stack[-15:]
        self.redo_stack.clear()

    def _restore_bytes(self, data):
        self.doc = pymupdf.open("pdf", data)
        self.refresh_all()
        self.mark_dirty()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.doc.tobytes())
            self._restore_bytes(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.doc.tobytes())
            self._restore_bytes(self.redo_stack.pop())

    def new_task(self):
        w = StartWindow()
        w.show()
        _windows.append(w)

    def open_dialog(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open", str(Path(self.path).parent if self.path else Path.home()),
                                            "PDF files (*.pdf);;" + D.OPEN_FILTER)
        if not fn:
            return
        if self.doc is None:
            self.load(fn)
        else:
            PdfEditor.open_window(fn)

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        rec = [r for r in settings()["recent_documents"] if Path(r).exists() and not r.endswith(D.PROJECT_EXT)][:12]
        for i, r in enumerate(rec):
            m.addAction(f"&{i + 1}. {Path(r).name}", lambda r=r: PdfEditor.open_window(r))
        if not rec:
            m.addAction("(none)").setEnabled(False)

    def _save_to(self, out):
        enc = self.encryption
        kw = dict(garbage=3, deflate=True)
        if enc and not enc.get("keep"):
            kw.update(encryption=enc["encryption"], user_pw=enc["user_pw"], owner_pw=enc["owner_pw"],
                      permissions=enc["permissions"])
        elif enc and enc.get("keep"):
            kw.update(encryption=pymupdf.PDF_ENCRYPT_KEEP)
        tmp = str(out) + ".folio-tmp"
        try:
            self.doc.save(tmp, **kw)
        except Exception:
            kw.pop("encryption", None)
            self.doc.save(tmp, **{k: v for k, v in kw.items() if k in ("garbage", "deflate")})
        os.replace(tmp, out)

    def save(self):
        if not self.doc:
            return False
        if self.untitled or not self.path:
            return self.save_as_pdf()
        try:
            self._save_to(self.path)
        except Exception as e:
            error(self, f"Cannot save the document.\n{e}")
            return False
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {self.path}", 4000)
        return True

    def save_as_pdf(self):
        if not self.doc:
            return False
        start = self.path if (self.path and not self.untitled) else str(Path.home() / (Path(self.path).stem if self.path else "Document") ) + ".pdf"
        fn, _ = QFileDialog.getSaveFileName(self, "Save As PDF Document", start, "PDF Document (*.pdf)")
        if not fn:
            return False
        if not fn.lower().endswith(".pdf"):
            fn += ".pdf"
        try:
            self._save_to(fn)
        except Exception as e:
            error(self, f"Cannot save file. Please choose another location.\n{e}")
            return False
        self.doc = pymupdf.open(fn) if not (self.encryption and self.encryption.get("user_pw")) else self.doc
        self.path, self.untitled, self.dirty = fn, False, False
        settings().add_recent("recent_documents", fn)
        self._update_title()
        return True

    def _temp_copy(self):
        tmp = str(Path(tempfile.mkdtemp(prefix="folio-pdf-")) / ((Path(self.path).stem if self.path else "Document") + ".pdf"))
        self.doc.save(tmp, garbage=1)
        return tmp

    def save_as_format(self, fmt):
        if not self.doc:
            return
        fn = save_dialog(self, fmt, Path(self.path).stem if self.path else "Document",
                         str(Path(self.path).parent) if self.path and not self.untitled else None)
        if not fn:
            return
        src = self._temp_copy()
        langs = settings()["languages"]

        def work(progress, cancel):
            if fmt == "pdf":
                return pipeline.make_pdf([src], fn, langs, True, progress=progress, cancel=cancel)
            if fmt == "pdf_image":
                pages = [exporters.ExportPage(lp.image) for lp in D.load_pages(src, dpi=200, with_text=False)]
                return exporters.export(pages, "pdf_image", fn)
            return pipeline.convert([src], fmt, fn, langs, progress=progress, cancel=cancel)
        run_task(self, "Saving...", work, on_done=lambda outs: (self.statusBar().showMessage(f"Saved {outs[0]}", 5000),
                                                                  paths.open_with_default(outs[0])))

    def save_page_images(self):
        if not self.doc:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Save Page Images", str(Path.home() / "Page.png"),
                                            "PNG (*.png);;JPEG (*.jpg);;Multi-page TIFF (*.tif)")
        if not fn:
            return
        ext = (Path(fn).suffix.lower().lstrip(".") or "png").replace("jpeg", "jpg").replace("tiff", "tif")
        src = self._temp_copy()
        run_task(self, "Saving page images...", lambda progress, cancel: exporters.export_images(
            [exporters.ExportPage(lp.image) for lp in D.load_pages(src, dpi=300, with_text=False)], fn, ext),
            on_done=lambda files: self.statusBar().showMessage(f"Saved {len(files)} file(s)", 5000))

    def closeEvent(self, e):
        if self.dirty and self.doc:
            r = ask(self, f"Save changes to {Path(self.path).name if self.path else 'the document'}?",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel or (r == QMessageBox.Save and not self.save()):
                e.ignore()
                return
        e.accept()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not files:
            return
        if self.doc is None:
            self.load(files[0])
        else:
            self.insert_files(files, len(self.doc))

    # ------------------------------------------------------------------ navigation
    def _page_changed(self, i):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        if self.thumbs.currentRow() != i:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(i)
            self.thumbs.blockSignals(False)

    def goto(self, i, record=True):
        if not self.doc or i is None:
            return
        i = max(0, min(int(i), len(self.doc) - 1))
        if record:
            self.history = self.history[:self.hist_pos + 1] + [self.view.cur_page, i]
            self.hist_pos = len(self.history) - 1
        self.view.go_to_page(i)

    def back(self):
        if self.hist_pos > 0:
            self.hist_pos -= 1
            self.goto(self.history[self.hist_pos], record=False)

    def forward(self):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self.goto(self.history[self.hist_pos], record=False)

    def goto_dialog(self):
        if self.doc:
            n, ok = QInputDialog.getInt(self, "Go to Page", "Page number:", self.view.cur_page + 1, 1, len(self.doc))
            if ok:
                self.goto(n - 1)

    def _zoom_box(self, idx):
        t = self.zoom_box.currentText()
        if t == "Fit Width":
            self.view.fit_width()
        elif t == "Best Fit":
            self.view.fit_page()
        else:
            try:
                self.view.set_zoom(float(t.strip("% ")) / 100)
            except ValueError:
                pass

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
            self.menuBar().show()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------------ tools & mouse
    def set_tool(self, key):
        self.tool = key
        if key in self.tool_acts:
            self.tool_acts[key].setChecked(True)
        if key in ("select", "hand"):
            self.view.set_tool(key)
        elif key == "ink":
            self.view.set_tool("ink")
        elif key in ("line", "arrow"):
            self.view.set_tool("line")
        else:
            self.view.set_tool("draw")
        hints = {"note": "Click where the note should go.", "highlight": "Drag over text to highlight it.",
                 "underline": "Drag over text to underline it.", "strike": "Drag over text to strike it through.",
                 "caret": "Click where text should be inserted.", "textbox": "Drag to draw a text box.",
                 "stamp": "Click or drag where the stamp should go.", "signature": "Click or drag where to sign.",
                 "edittext": "Drag a rectangle around the text to edit.", "picture": "Drag a rectangle for the picture.",
                 "link": "Drag a rectangle for the link.", "erase": "Drag over content to erase it.",
                 "crop": "Drag the area to keep.", "redact": "Drag over content to mark it for redaction."}
        self.statusBar().showMessage(hints.get(key, ""))

    def _page_disp_rect(self, i, r: QRectF):
        return pymupdf.Rect(r.x(), r.y(), r.x() + r.width(), r.y() + r.height())

    def _words_in(self, page, urect):
        ws = [w for w in page.get_text("words") if pymupdf.Rect(w[:4]).intersects(urect)]
        return ws

    def _line_quads(self, ws):
        lines = {}
        for w in ws:
            lines.setdefault((w[5], w[6]), []).append(pymupdf.Rect(w[:4]))
        out = []
        for rs in lines.values():
            r = rs[0]
            for x in rs[1:]:
                r |= x
            out.append(r)
        return out

    def _rect(self, i, r: QRectF):
        if not self.doc or i < 0:
            return
        page = self.doc[i]
        dr = self._page_disp_rect(i, r)
        ur = to_unrot(page, dr)
        t = self.tool
        if t == "select":
            self.selection = (i, dr)
            self.view.set_overlays("sel", [(i, r)], pen=QPen(QColor(30, 100, 220), 1, Qt.DashLine),
                                   brush=QBrush(QColor(30, 100, 220, 30)))
            txt = page.get_textbox(ur).strip()
            self.statusBar().showMessage(f"Selected {len(txt)} characters. Ctrl+C copies; Edit ▸ Copy As for image/table.")
            return
        if t in ("highlight", "underline", "strike", "redact", "erase", "rect", "oval", "textbox", "picture", "link",
                 "edittext", "crop", "stamp", "signature"):
            if t not in ("crop",):
                self.push_undo()
        if t in ("highlight", "underline", "strike"):
            ws = self._words_in(page, ur)
            quads = self._line_quads(ws) or [ur]
            fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
                  "strike": page.add_strikeout_annot}[t]
            a = fn(quads)
            a.set_info(title=os.environ.get("USER", "Folio"))
            a.update()
        elif t == "rect":
            a = page.add_rect_annot(ur)
            a.set_colors(stroke=(0.85, 0.1, 0.1))
            a.set_border(width=1.5)
            a.update()
        elif t == "oval":
            a = page.add_circle_annot(ur)
            a.set_colors(stroke=(0.85, 0.1, 0.1))
            a.set_border(width=1.5)
            a.update()
        elif t == "textbox":
            txt, ok = QInputDialog.getMultiLineText(self, "Text Box", "Text:")
            if not ok or not txt:
                self.undo_stack.pop()
                return
            a = page.add_freetext_annot(ur, txt, fontsize=11, text_color=(0, 0, 0), fill_color=(1, 1, 0.85),
                                        rotate=page.rotation)
            a.set_border(width=0.8)
            a.set_info(title=os.environ.get("USER", "Folio"))
            a.update()
        elif t == "redact":
            a = page.add_redact_annot(ur, fill=(0, 0, 0))
            a.update()
            self.statusBar().showMessage("Area marked for redaction. Use 'Apply Redactions' to remove the content permanently.")
        elif t == "erase":
            page.add_redact_annot(ur, fill=(1, 1, 1))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS, graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED
                                  if hasattr(pymupdf, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED") else 0)
        elif t == "picture":
            fn, _ = QFileDialog.getOpenFileName(self, "Add Picture", str(Path.home()), D.IMAGE_FILTER)
            if not fn:
                self.undo_stack.pop()
                return
            page.insert_image(ur, filename=fn, keep_proportion=True, rotate=0)
        elif t == "link":
            dlg = PD.LinkDialog(len(self.doc), self)
            if not dlg.exec():
                self.undo_stack.pop()
                return
            page.insert_link(dlg.link(ur))
        elif t == "edittext":
            self._edit_text(page, ur)
        elif t == "crop":
            self.crop(i, ur)
            return
        elif t == "stamp":
            self._place_stamp(page, ur)
        elif t == "signature":
            self._place_signature(page, ur)
        else:
            return
        self.mark_dirty()
        self.refresh_pages([i])

    def _click(self, i, pt: QPointF):
        if not self.doc or i < 0:
            return
        page = self.doc[i]
        dp = pymupdf.Point(pt.x(), pt.y())
        up = dp * page.derotation_matrix if page.rotation else dp
        t = self.tool
        if t == "select":
            self.clear_selection()
            for w in page.widgets() or []:
                if up in w.rect:
                    self._edit_widget(page, w, i)
                    return
            for l in page.get_links():
                if up in l["from"]:
                    if l.get("kind") == pymupdf.LINK_GOTO:
                        self.goto(l.get("page", 0))
                    elif l.get("uri"):
                        if ask(self, f"Open the link?\n{l['uri']}") == QMessageBox.Yes:
                            paths.open_with_default(l["uri"])
                    return
            for a in page.annots() or []:
                if up in a.rect:
                    self._sel_annot = (i, a.xref)
                    c = a.info.get("content", "")
                    self.statusBar().showMessage(f"{a.type[1]}: {c[:120]}  (Delete removes it; double-click edits)")
                    return
            return
        if t == "note":
            txt, ok = QInputDialog.getMultiLineText(self, "Add Note", "Note:")
            if ok and txt:
                self.push_undo()
                a = page.add_text_annot(up, txt, icon="Comment")
                a.set_info(title=os.environ.get("USER", "Folio"))
                a.update()
        elif t == "caret":
            txt, ok = QInputDialog.getText(self, "Insert Text", "Text to insert:")
            if ok and txt:
                self.push_undo()
                a = page.add_caret_annot(up)
                a.set_info(title=os.environ.get("USER", "Folio"), content=txt)
                a.update()
        elif t in ("stamp", "signature"):
            self.push_undo()
            w = 160 if t == "stamp" else 150
            r = to_unrot(page, pymupdf.Rect(dp.x - w / 2, dp.y - 25, dp.x + w / 2, dp.y + 25))
            (self._place_stamp if t == "stamp" else self._place_signature)(page, r)
        elif t == "textbox":
            self._rect(i, QRectF(pt.x(), pt.y(), 180, 40))
            return
        else:
            return
        self.mark_dirty()
        self.refresh_pages([i])

    def _dclick(self, i, pt):
        if not self.doc or i < 0 or self.tool != "select":
            return
        page = self.doc[i]
        dp = pymupdf.Point(pt.x(), pt.y())
        up = dp * page.derotation_matrix if page.rotation else dp
        for a in page.annots() or []:
            if up in a.rect:
                self._edit_annot(i, a)
                return

    def _ink(self, i, pts):
        page = self.doc[i]
        self.push_undo()
        stroke = [((pymupdf.Point(p.x(), p.y()) * page.derotation_matrix) if page.rotation else pymupdf.Point(p.x(), p.y()))
                  for p in pts]
        a = page.add_ink_annot([[(q.x, q.y) for q in stroke]])
        a.set_colors(stroke=(0.1, 0.35, 0.85))
        a.set_border(width=2)
        a.update()
        self.mark_dirty()
        self.refresh_pages([i])

    def _line(self, i, p1, p2):
        page = self.doc[i]
        self.push_undo()
        m = page.derotation_matrix if page.rotation else pymupdf.Identity
        a = page.add_line_annot(pymupdf.Point(p1.x(), p1.y()) * m, pymupdf.Point(p2.x(), p2.y()) * m)
        a.set_colors(stroke=(0.85, 0.1, 0.1))
        a.set_border(width=1.5)
        if self.tool == "arrow":
            a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        a.update()
        self.mark_dirty()
        self.refresh_pages([i])

    def _context(self, i, pt, gpos):
        m = QMenu(self)
        if self.selection:
            m.addAction("&Copy Text", self.copy_text)
            m.addAction("Copy as &Image", self.copy_image)
            m.addAction("Copy as &Table", self.copy_table)
            m.addSeparator()
            m.addAction("&Highlight", lambda: self._markup_selection("highlight"))
            m.addAction("&Underline", lambda: self._markup_selection("underline"))
            m.addAction("&Strikethrough", lambda: self._markup_selection("strike"))
            m.addAction("&Redact", lambda: self._markup_selection("redact"))
            m.addSeparator()
        m.addAction(icon("tool-select"), "Select Objects", lambda: self.set_tool("select"))
        m.addAction(icon("tool-hand"), "Hand Tool", lambda: self.set_tool("hand"))
        m.addSeparator()
        m.addAction(icon("note"), "Add &Note", lambda: self.set_tool("note"))
        m.addAction(icon("bookmark"), "Add Book&mark", self.add_bookmark)
        m.addSeparator()
        m.addAction(icon("rotate-right"), "Rotate Page Right", lambda: self.rotate_pages(90, [i]))
        m.addAction(icon("rotate-left"), "Rotate Page Left", lambda: self.rotate_pages(-90, [i]))
        m.addAction(icon("page-delete"), "Delete Page", lambda: self.delete_pages([i]))
        m.exec(gpos)

    def _markup_selection(self, kind):
        if not self.selection:
            return
        i, dr = self.selection
        saved = self.tool
        self.tool = kind
        self._rect(i, QRectF(dr.x0, dr.y0, dr.width, dr.height))
        self.tool = saved
        self.clear_selection()

    def clear_selection(self):
        self.selection = None
        self.view.clear_overlays("sel")

    def select_all(self):
        if self.doc:
            i = self.view.cur_page
            r = self.doc[i].rect
            self.selection = (i, r)
            self.view.set_overlays("sel", [(i, QRectF(0, 0, r.width, r.height))], brush=QBrush(QColor(30, 100, 220, 25)))

    def _sel_text(self):
        if self.selection:
            i, dr = self.selection
            p = self.doc[i]
            return p.get_textbox(to_unrot(p, dr))
        if self.doc:
            return self.doc[self.view.cur_page].get_text("text")
        return ""

    def copy_text(self):
        QGuiApplication.clipboard().setText(self._sel_text())
        self.statusBar().showMessage("Text copied to the Clipboard.", 3000)

    def copy_image(self):
        if not self.doc:
            return
        i, dr = self.selection or (self.view.cur_page, self.doc[self.view.cur_page].rect)
        p = self.doc[i]
        pix = p.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=to_unrot(p, dr) if p.rotation else dr, alpha=False)
        QGuiApplication.clipboard().setImage(QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy())
        self.statusBar().showMessage("Image copied to the Clipboard.", 3000)

    def copy_table(self):
        if not self.doc:
            return
        i, dr = self.selection or (self.view.cur_page, self.doc[self.view.cur_page].rect)
        p = self.doc[i]
        ws = self._words_in(p, to_unrot(p, dr))
        words = [ocr.Word(w[4], (int(w[0] * 4), int(w[1] * 4), int((w[2] - w[0]) * 4), int((w[3] - w[1]) * 4))) for w in ws]
        rows = build_table(words_to_lines(words))
        QGuiApplication.clipboard().setText("\n".join("\t".join(r) for r in rows))
        self.statusBar().showMessage(f"Table with {len(rows)} row(s) copied to the Clipboard.", 3000)

    # ------------------------------------------------------------------ annotations / comments pane
    def refresh_comments(self):
        self.comments.clear()
        if not self.doc:
            return
        for i, p in enumerate(self.doc):
            for a in p.annots() or []:
                if a.type[0] in (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_POPUP):
                    continue
                inf = a.info
                txt = inf.get("content", "")
                if not txt and a.type[0] in (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE,
                                             pymupdf.PDF_ANNOT_STRIKE_OUT, pymupdf.PDF_ANNOT_REDACT):
                    txt = p.get_textbox(a.rect).strip().replace("\n", " ")
                it = QListWidgetItem(f"{a.type[1]} — page {i + 1}\n{inf.get('title', '')}: {txt[:200]}")
                it.setData(Qt.UserRole, (i, a.xref))
                self.comments.addItem(it)
        self.comments_dock.setWindowTitle(f"Comments ({self.comments.count()})")

    def _annot(self, i, xref):
        for a in self.doc[i].annots() or []:
            if a.xref == xref:
                return a
        return None

    def _comment_go(self, it):
        i, xref = it.data(Qt.UserRole)
        a = self._annot(i, xref)
        if a:
            r = to_disp(self.doc[i], a.rect)
            self.view.ensure_visible(i, QRectF(r.x0, r.y0, r.width, r.height))
            self.view.set_overlays("annot", [(i, QRectF(r.x0 - 3, r.y0 - 3, r.width + 6, r.height + 6))],
                                   pen=QPen(QColor(230, 60, 30), 2), brush=QBrush(Qt.NoBrush))
            QTimer.singleShot(1500, lambda: self.view.clear_overlays("annot"))
            self._sel_annot = (i, xref)

    def _comment_edit(self, it):
        i, xref = it.data(Qt.UserRole)
        a = self._annot(i, xref)
        if a:
            self._edit_annot(i, a)

    def _edit_annot(self, i, a):
        dlg = PD.AnnotationDialog(a, self)
        if dlg.exec():
            self.push_undo()
            a.set_info(title=dlg.author.text(), content=dlg.content.toPlainText())
            a.set_opacity(dlg.op.value() / 100)
            a.update()
            self.mark_dirty()
            self.refresh_pages([i])

    def _comment_menu(self, pos):
        it = self.comments.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        m.addAction("&Properties...", lambda: self._comment_edit(it))
        m.addAction("&Delete", lambda: self._delete_annot(*it.data(Qt.UserRole)))
        m.exec(self.comments.mapToGlobal(pos))

    def _delete_annot(self, i, xref):
        a = self._annot(i, xref)
        if a:
            self.push_undo()
            self.doc[i].delete_annot(a)
            self.mark_dirty()
            self.refresh_pages([i])

    def delete_selected_annot(self):
        sa = getattr(self, "_sel_annot", None)
        if sa:
            self._delete_annot(*sa)
            self._sel_annot = None

    # ------------------------------------------------------------------ stamps, signatures, text edit
    def _choose_stamp(self, st):
        self.stamp = st
        self.set_tool("stamp")

    def create_text_stamp(self):
        dlg = PD.TextStampDialog(self)
        if dlg.exec():
            self._choose_stamp(("text", dlg.text.text(), dlg.color.currentData(), dlg.size.value()))

    def create_image_stamp(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Create Stamp from Image", str(Path.home()), D.IMAGE_FILTER)
        if fn:
            self._choose_stamp(("image", fn))

    def _place_stamp(self, page, ur):
        st = self.stamp
        if st[0] == "builtin":
            a = page.add_stamp_annot(ur, stamp=st[1])
            a.set_info(title=os.environ.get("USER", "Folio"))
            a.update()
        elif st[0] == "text":
            now = datetime.datetime.now()
            txt = st[1].replace("<<date>>", now.strftime("%d/%m/%Y")).replace("<<time>>", now.strftime("%H:%M"))
            a = page.add_freetext_annot(ur, txt, fontsize=st[3], fontname="Helv", text_color=st[2],
                                        align=1, rotate=page.rotation)
            a.set_border(width=2)
            a.set_info(title=os.environ.get("USER", "Folio"), subject="Stamp")
            a.update()
        else:
            page.insert_image(ur, filename=st[1], keep_proportion=True)

    def _signature_tool(self):
        if not self.signature:
            sigs = settings().get("signatures", [])
            if sigs:
                self.signature = sigs[0]
            else:
                self.create_text_signature()
                if not self.signature:
                    return
        self.set_tool("signature")

    def create_image_signature(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Create Signature from Image", str(Path.home()), D.IMAGE_FILTER)
        if fn:
            self.signature = ["image", fn]
            self._remember_sig()
            self.set_tool("signature")

    def create_text_signature(self):
        name, ok = QInputDialog.getText(self, "Create Text Signature", "Your name:", text=os.environ.get("USER", ""))
        if ok and name:
            self.signature = ["text", name]
            self._remember_sig()
            self.set_tool("signature")

    def _remember_sig(self):
        s = settings()
        sigs = [x for x in s.get("signatures", []) if x != self.signature]
        s["signatures"] = [self.signature] + sigs[:4]

    def _place_signature(self, page, ur):
        sig = self.signature
        if not sig:
            return
        if sig[0] == "image":
            page.insert_image(ur, filename=sig[1], keep_proportion=True)
        else:
            fs = min(ur.height * 0.7, 28)
            page.insert_textbox(ur, sig[1], fontsize=fs, fontname="tiit", color=(0.05, 0.2, 0.6), align=1,
                                rotate=page.rotation)
            d = pymupdf.Point(ur.x0, ur.y1 - 2), pymupdf.Point(ur.x1, ur.y1 - 2)
            page.draw_line(*d, color=(0.4, 0.4, 0.4), width=0.6)

    def _edit_text(self, page, ur):
        spans = [s for b in page.get_text("dict", clip=ur)["blocks"] for l in b.get("lines", []) for s in l["spans"]]
        size = round(sum(s["size"] for s in spans) / len(spans), 1) if spans else 11
        color = spans[0]["color"] if spans else 0
        old = page.get_textbox(ur).rstrip()
        dlg = PD.EditTextDialog(old, size, self)
        if not dlg.exec():
            self.undo_stack.pop()
            return
        new = dlg.edit.toPlainText()
        page.add_redact_annot(ur, fill=None)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        rgb = ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255
        fontfile = D.unicode_font_file()
        fs = dlg.size.value()
        kw = dict(fontname="FolioEd", fontfile=fontfile) if fontfile else dict(fontname="helv")
        box = pymupdf.Rect(ur.x0, ur.y0, ur.x1 + 2, ur.y1 + fs)
        while fs > 4:
            rc = page.insert_textbox(box, new, fontsize=fs, color=rgb, **kw)
            if rc >= 0:
                break
            fs -= 0.5

    # ------------------------------------------------------------------ forms
    def _edit_widget(self, page, w, i):
        self.push_undo()
        ft = w.field_type
        if ft == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            w.field_value = w.on_state() if w.field_value in (False, "Off", "") else "Off"
        elif ft == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            w.field_value = True
        elif ft in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX):
            ch = [c if isinstance(c, str) else c[0] for c in (w.choice_values or [])]
            if not ch:
                return
            v, ok = QInputDialog.getItem(self, w.field_name or "Field", "Value:", ch,
                                         ch.index(w.field_value) if w.field_value in ch else 0, False)
            if not ok:
                return
            w.field_value = v
        elif ft == pymupdf.PDF_WIDGET_TYPE_TEXT:
            v, ok = QInputDialog.getMultiLineText(self, w.field_name or "Field", "Value:", str(w.field_value or ""))
            if not ok:
                return
            w.field_value = v
        else:
            return
        w.update()
        self.mark_dirty()
        self.refresh_pages([i])

    def highlight_fields(self, on):
        self._fields_on = on
        if not on or not self.doc:
            self.view.clear_overlays("fields")
            return
        rects = []
        for i, p in enumerate(self.doc):
            for w in p.widgets() or []:
                r = to_disp(p, w.rect)
                rects.append((i, QRectF(r.x0, r.y0, r.width, r.height)))
        self.view.set_overlays("fields", rects, brush=QBrush(QColor(80, 140, 255, 50)), pen=QPen(QColor(80, 140, 255), 1))

    def save_form(self):
        if not self.doc:
            return
        data = {w.field_name: w.field_value for p in self.doc for w in (p.widgets() or []) if w.field_name}
        if not data:
            info(self, "This document has no form fields.")
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Save Form Data", str(Path.home() / "form-data.json"), "JSON (*.json)")
        if fn:
            Path(fn).write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), "utf-8")

    def load_form(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Load Form Data", str(Path.home()), "JSON (*.json)")
        if not fn:
            return
        data = json.loads(Path(fn).read_text("utf-8"))
        self.push_undo()
        n = 0
        for p in self.doc:
            for w in p.widgets() or []:
                if w.field_name in data:
                    w.field_value = data[w.field_name]
                    w.update()
                    n += 1
        self.mark_dirty()
        self.refresh_pages(list(range(len(self.doc))))
        self.statusBar().showMessage(f"{n} field(s) filled.", 4000)

    def reset_form(self):
        self.push_undo()
        for p in self.doc:
            for w in p.widgets() or []:
                w.field_value = "Off" if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON) else ""
                w.update()
        self.mark_dirty()
        self.refresh_pages(list(range(len(self.doc))))

    # ------------------------------------------------------------------ pages
    def _pages_moved(self, order):
        self.push_undo()
        self.doc.select(order)
        self.mark_dirty()
        self.refresh_all()

    def _selected_pages(self):
        idx = self.thumbs.selected_indices()
        return idx or [self.view.cur_page]

    def _thumb_menu(self, pos):
        m = QMenu(self)
        m.addAction(icon("rotate-right"), "Rotate &Right", lambda: self.rotate_pages(90))
        m.addAction(icon("rotate-left"), "Rotate &Left", lambda: self.rotate_pages(-90))
        m.addSeparator()
        m.addAction(icon("page-add"), "Add Pages from &File...", self.add_pages_from_file)
        m.addAction("Add &Blank Page", self.add_blank_page)
        m.addAction("R&eplace Pages...", self.replace_pages)
        m.addAction("E&xtract Pages...", self.extract_pages)
        m.addAction(icon("recognize"), "Re&cognize Pages", lambda: self.recognize(self._selected_pages()))
        m.addSeparator()
        m.addAction(icon("page-delete"), "&Delete Pages", self.delete_pages)
        m.exec(self.thumbs.mapToGlobal(pos))

    def rotate_pages(self, deg, pages=None):
        if not self.doc:
            return
        self.push_undo()
        for i in pages or self._selected_pages():
            p = self.doc[i]
            p.set_rotation((p.rotation + deg) % 360)
        self.mark_dirty()
        self.refresh_all()

    def delete_pages(self, pages=None):
        if not self.doc:
            return
        pages = pages or self._selected_pages()
        if len(pages) >= len(self.doc):
            error(self, "A document must keep at least one page.")
            return
        if ask(self, f"Delete {len(pages)} page(s)?") != QMessageBox.Yes:
            return
        self.push_undo()
        self.doc.delete_pages(sorted(pages))
        self.mark_dirty()
        self.refresh_all()

    def add_blank_page(self):
        if not self.doc:
            self.doc = pymupdf.open()
        self.push_undo()
        at = self.view.cur_page + 1 if len(self.doc) else 0
        r = self.doc[self.view.cur_page].rect if len(self.doc) else pymupdf.paper_rect("a4")
        self.doc.new_page(at, width=r.width, height=r.height)
        self.mark_dirty()
        self.refresh_all()
        self.goto(at)

    def insert_files(self, files, at):
        langs = settings()["languages"]
        out = str(Path(tempfile.mkdtemp(prefix="folio-ins-")) / "insert.pdf")

        def work(progress, cancel):
            return pipeline.make_pdf(files, out, langs, recognize=True, progress=progress, cancel=cancel)

        def done(_):
            self.push_undo()
            src = pymupdf.open(out)
            self.doc.insert_pdf(src, start_at=at)
            self.mark_dirty()
            self.refresh_all()
            self.goto(at)
        run_task(self, "Adding pages...", work, on_done=done)

    def add_pages_from_file(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Add from File", str(Path.home()), D.OPEN_FILTER)
        if fns:
            if self.doc is None:
                self.doc = pymupdf.open()
            self.insert_files(fns, self.view.cur_page + 1 if len(self.doc) else 0)

    def add_pages_from_scanner(self):
        dlg = ScanDialog(self)
        if dlg.exec() and dlg.images:
            tmp = Path(tempfile.mkdtemp(prefix="folio-scan-"))
            files = []
            for i, img in enumerate(dlg.images):
                p = tmp / f"scan{i}.png"
                img.save(p, dpi=img.info.get("dpi", (300, 300)))
                files.append(str(p))
            if self.doc is None:
                self.doc = pymupdf.open()
            self.insert_files(files, self.view.cur_page + 1 if len(self.doc) else 0)

    def replace_pages(self):
        if not self.doc:
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Replace Pages", str(Path.home()), D.OPEN_FILTER)
        if not fn:
            return
        try:
            src = pymupdf.open(D.as_pdf_path(fn)) if D.kind_of(fn) != "image" else None
            if src is None:
                tmp = str(Path(tempfile.mkdtemp()) / "img.pdf")
                pipeline.make_pdf([fn], tmp, recognize=False)
                src = pymupdf.open(tmp)
        except Exception as e:
            error(self, e)
            return
        sel = self._selected_pages()
        start = sel[0]
        n, ok = QInputDialog.getInt(self, "Replace Pages",
                                    f"Replace {len(sel)} page(s) starting at page {start + 1} with pages from\n"
                                    f"{Path(fn).name} starting at page:", 1, 1, len(src))
        if not ok:
            return
        cnt = min(len(sel), len(src) - (n - 1))
        self.push_undo()
        self.doc.delete_pages(list(range(start, start + cnt)))
        self.doc.insert_pdf(src, from_page=n - 1, to_page=n - 2 + cnt, start_at=start)
        self.mark_dirty()
        self.refresh_all()

    def extract_pages(self):
        if not self.doc:
            return
        sel = self._selected_pages()
        fn, _ = QFileDialog.getSaveFileName(self, "Extract Pages", str(Path.home() / "Extracted pages.pdf"), "PDF (*.pdf)")
        if fn:
            out = pymupdf.open()
            for i in sel:
                out.insert_pdf(self.doc, from_page=i, to_page=i)
            out.save(fn, garbage=3, deflate=True)
            self.statusBar().showMessage(f"Saved {len(sel)} page(s) to {fn}", 5000)

    def crop(self, i, ur):
        r = ask(self, "Apply this crop to all pages?\n\nYes: all pages   No: only this page",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return
        self.push_undo()
        pages = range(len(self.doc)) if r == QMessageBox.Yes else [i]
        for k in pages:
            p = self.doc[k]
            try:
                mb = p.mediabox
                cr = pymupdf.Rect(ur) & p.rect if not p.rotation else pymupdf.Rect(ur)
                # cropbox is expressed in unrotated, mediabox-relative coordinates
                p.set_cropbox(pymupdf.Rect(cr.x0 + p.cropbox.x0, cr.y0 + p.cropbox.y0,
                                           cr.x1 + p.cropbox.x0, cr.y1 + p.cropbox.y0) & mb)
            except Exception as e:
                error(self, f"Cannot crop page {k + 1}: {e}")
                break
        self.set_tool("select")
        self.mark_dirty()
        self.refresh_all()

    def correct_orientation(self):
        if not self.doc:
            return
        data = self.doc.tobytes()

        def work(progress, cancel):
            d = pymupdf.open("pdf", data)
            rots = []
            for i, p in enumerate(d):
                if cancel():
                    break
                progress(i, len(d), f"Checking page {i + 1} of {len(d)}...")
                pix = p.get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                rots.append(ocr.detect_orientation(img))
            return rots

        def done(rots):
            if not any(rots):
                info(self, "All pages are correctly oriented.")
                return
            self.push_undo()
            for i, r in enumerate(rots):
                if r:
                    p = self.doc[i]
                    p.set_rotation((p.rotation + r) % 360)
            self.mark_dirty()
            self.refresh_all()
            self.statusBar().showMessage(f"Rotated {sum(1 for r in rots if r)} page(s).", 5000)
        run_task(self, "Correcting orientation...", work, on_done=done)

    def enhance_images(self):
        if not self.doc:
            return
        from ..core import imageproc
        sel = self._selected_pages()
        if ask(self, f"Enhance the page images of {len(sel)} page(s)? (deskew, background cleanup)\n"
                     "The pages will be replaced by processed images; the text layer is recognized again.") != QMessageBox.Yes:
            return
        data = self.doc.tobytes()
        langs = settings()["languages"]

        def work(progress, cancel):
            d = pymupdf.open("pdf", data)
            out = []
            for n, i in enumerate(sel):
                progress(n, len(sel), f"Enhancing page {i + 1}...")
                p = d[i]
                pix = p.get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                img.info["dpi"] = (300, 300)
                img = imageproc.autocontrast(imageproc.deskew(img))
                ep = exporters.ExportPage(img, ocr.recognize(img, langs) if ocr.available() else None)
                one = pymupdf.open()
                pg = one.new_page(width=p.rect.width, height=p.rect.height)
                data_, _ = exporters._img_bytes(img)
                pg.insert_image(pg.rect, stream=data_)
                if ep.ocr:
                    exporters.add_text_layer(pg, ep, pg.rect.width / img.width)
                out.append((i, one.tobytes()))
            return out

        def done(out):
            self.push_undo()
            for i, b in out:
                src = pymupdf.open("pdf", b)
                self.doc.delete_page(i)
                self.doc.insert_pdf(src, start_at=i)
            self.mark_dirty()
            self.refresh_all()
        run_task(self, "Enhancing page images...", work, on_done=done)

    # ------------------------------------------------------------------ bookmarks & attachments
    def refresh_bookmarks(self):
        self.bm.clear()
        if not self.doc:
            return
        stack = [self.bm.invisibleRootItem()]
        for lvl, title, page, *_ in self.doc.get_toc():
            while len(stack) > lvl:
                stack.pop()
            it = QTreeWidgetItem([title])
            it.setData(0, Qt.UserRole, max(0, page - 1))
            stack[-1].addChild(it)
            stack.append(it)
        self.bm.expandAll()

    def add_bookmark(self):
        if not self.doc:
            return
        t, ok = QInputDialog.getText(self, "Add Bookmark", "Bookmark title:", text=f"Page {self.view.cur_page + 1}")
        if ok and t:
            self.push_undo()
            toc = self.doc.get_toc()
            toc.append([1, t, self.view.cur_page + 1])
            toc.sort(key=lambda x: x[2]) if all(x[0] == 1 for x in toc) else None
            self.doc.set_toc(toc)
            self.mark_dirty()
            self.refresh_bookmarks()

    def _bm_menu(self, pos):
        it = self.bm.itemAt(pos)
        m = QMenu(self)
        m.addAction("&Add Bookmark...", self.add_bookmark)
        if it:
            m.addAction("&Rename...", lambda: self._bm_edit(it, "rename"))
            m.addAction("&Delete", lambda: self._bm_edit(it, "delete"))
        m.exec(self.bm.mapToGlobal(pos))

    def _bm_edit(self, it, op):
        flat = []

        def walk(node):
            for k in range(node.childCount()):
                flat.append(node.child(k))
                walk(node.child(k))
        walk(self.bm.invisibleRootItem())
        idx = flat.index(it)
        toc = self.doc.get_toc()
        self.push_undo()
        if op == "rename":
            t, ok = QInputDialog.getText(self, "Rename Bookmark", "Title:", text=toc[idx][1])
            if not ok:
                return
            toc[idx][1] = t
        else:
            lvl = toc[idx][0]
            end = idx + 1
            while end < len(toc) and toc[end][0] > lvl:
                end += 1
            del toc[idx:end]
        self.doc.set_toc(toc)
        self.mark_dirty()
        self.refresh_bookmarks()

    def refresh_attachments(self):
        self.att.clear()
        if self.doc:
            for n in self.doc.embfile_names():
                self.att.addItem(QListWidgetItem(icon("attachment"), n))

    def add_attachment(self):
        if not self.doc:
            return
        fns, _ = QFileDialog.getOpenFileNames(self, "Add Attachment", str(Path.home()))
        if fns:
            self.push_undo()
            for f in fns:
                name = Path(f).name
                if name in self.doc.embfile_names():
                    self.doc.embfile_del(name)
                self.doc.embfile_add(name, Path(f).read_bytes(), filename=name)
            self.mark_dirty()
            self.refresh_attachments()
            self.nav_dock.show()
            self.nav.setCurrentIndex(3)

    def save_attachment(self, name=None):
        it = self.att.currentItem()
        name = name or (it.text() if it else None)
        if not name:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Save Attachment", str(Path.home() / name))
        if fn:
            Path(fn).write_bytes(self.doc.embfile_get(name))

    def delete_attachment(self):
        it = self.att.currentItem()
        if it:
            self.push_undo()
            self.doc.embfile_del(it.text())
            self.mark_dirty()
            self.refresh_attachments()

    # ------------------------------------------------------------------ search
    def find(self):
        self.nav_dock.show()
        self.nav.setCurrentIndex(2)
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def search(self):
        t = self.search_edit.text()
        self.search_res.clear()
        self.results = []
        if not t or not self.doc:
            self.view.clear_overlays("search")
            return
        for i, p in enumerate(self.doc):
            hits = p.search_for(t)
            if self.search_case.isChecked():
                hits = [h for h in hits if t in p.get_textbox(h + (-1, -1, 1, 1))]
            for h in hits:
                self.results.append((i, h))
                ctx = p.get_textbox(h + (-120, -2, 120, 2)).replace("\n", " ").strip()
                it = QListWidgetItem(f"Page {i + 1}: …{ctx}…")
                it.setData(Qt.UserRole, len(self.results) - 1)
                self.search_res.addItem(it)
        rects = []
        for i, h in self.results:
            d = to_disp(self.doc[i], h)
            rects.append((i, QRectF(d.x0, d.y0, d.width, d.height)))
        self.view.set_overlays("search", rects, brush=QBrush(QColor(255, 140, 0, 90)))
        self.statusBar().showMessage(f"{len(self.results)} result(s) found.")
        self._res_pos = -1
        if self.results:
            self.search_res.setCurrentRow(0)
            self._search_go(self.search_res.item(0))

    def _search_go(self, it):
        k = it.data(Qt.UserRole)
        self._res_pos = k
        i, h = self.results[k]
        d = to_disp(self.doc[i], h)
        self.view.ensure_visible(i, QRectF(d.x0, d.y0, d.width, d.height))
        self.view.set_overlays("cur", [(i, QRectF(d.x0 - 2, d.y0 - 2, d.width + 4, d.height + 4))],
                               pen=QPen(QColor(220, 40, 20), 2), brush=QBrush(Qt.NoBrush), z=7)

    def find_next(self):
        if not getattr(self, "results", None):
            return self.find()
        k = (getattr(self, "_res_pos", -1) + 1) % len(self.results)
        self.search_res.setCurrentRow(k)
        self._search_go(self.search_res.item(k))

    def markup_results(self, kind):
        if not getattr(self, "results", None):
            return
        self.push_undo()
        touched = set()
        for i, h in self.results:
            p = self.doc[i]
            if kind == "redact":
                p.add_redact_annot(h, fill=(0, 0, 0))
            else:
                {"highlight": p.add_highlight_annot, "underline": p.add_underline_annot,
                 "strike": p.add_strikeout_annot}[kind](h)
            touched.add(i)
        self.view.clear_overlays("search")
        self.mark_dirty()
        self.refresh_pages(sorted(touched))
        if kind == "redact":
            self.statusBar().showMessage("Results marked for redaction. Use Apply Redactions to remove them.")

    # ------------------------------------------------------------------ document tools
    def apply_redactions(self):
        if not self.doc:
            return
        n = sum(1 for p in self.doc for a in (p.annots() or []) if a.type[0] == pymupdf.PDF_ANNOT_REDACT)
        if not n:
            info(self, "No areas are marked for redaction. Use Redaction Mode to mark content first.")
            return
        if ask(self, f"Permanently remove the content of {n} marked area(s)?") != QMessageBox.Yes:
            return
        self.push_undo()
        for p in self.doc:
            p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        self.mark_dirty()
        self.refresh_all()

    def security(self):
        if not self.doc:
            return
        dlg = PD.SecurityDialog(self)
        if dlg.exec():
            self.encryption = dlg.result_options()
            self.mark_dirty()
            info(self, "Password security will be applied when the document is saved." if self.encryption else
                       "Password protection will be removed when the document is saved.")

    def reduce_size(self):
        if not self.doc:
            return
        dlg = PD.ReduceDialog(self)
        if not dlg.exec():
            return
        dpi, q = dlg.q.currentData()
        gray, fonts = dlg.gray.isChecked(), dlg.fonts.isChecked()
        before = len(self.doc.tobytes(garbage=0))
        data = self.doc.tobytes()

        def work(progress, cancel):
            d = pymupdf.open("pdf", data)
            progress(0, 2, "Compressing pictures...")
            d.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=q, set_to_gray=gray)
            if fonts:
                progress(1, 2, "Subsetting fonts...")
                try:
                    d.subset_fonts()
                except Exception:
                    pass
            return d.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)

        def done(b):
            self.push_undo()
            self.doc = pymupdf.open("pdf", b)
            self.mark_dirty()
            self.refresh_all()
            info(self, f"File size: {before / 1024:.0f} KB → {len(b) / 1024:.0f} KB. Save the document to keep the result.")
        run_task(self, "Reducing file size...", work, on_done=done)

    def properties(self):
        if not self.doc:
            return
        dlg = PD.PropertiesDialog(self.doc, self.path, self)
        if dlg.exec():
            self.push_undo()
            dlg.apply()
            self.mark_dirty()

    def delete_data(self):
        if not self.doc:
            return
        dlg = PD.DeleteDataDialog(self)
        if not dlg.exec():
            return
        self.push_undo()
        if dlg.comments.isChecked():
            self._delete_comments()
        if dlg.bookmarks.isChecked():
            self.doc.set_toc([])
        if dlg.attachments.isChecked():
            for n in self.doc.embfile_names():
                self.doc.embfile_del(n)
        self.doc.scrub(attached_files=dlg.attachments.isChecked(), clean_pages=True,
                       embedded_files=dlg.attachments.isChecked(), hidden_text=dlg.hidden.isChecked(),
                       javascript=dlg.links.isChecked(), metadata=dlg.metadata.isChecked(), redactions=False,
                       remove_links=dlg.links.isChecked(), reset_fields=dlg.links.isChecked(),
                       reset_responses=dlg.comments.isChecked(), thumbnails=True, xml_metadata=dlg.metadata.isChecked())
        self.mark_dirty()
        self.refresh_all()

    def _delete_comments(self):
        for p in self.doc:
            for a in list(p.annots() or []):
                if a.type[0] not in (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK):
                    p.delete_annot(a)

    def delete_comments(self):
        if self.doc and ask(self, "Delete all comments and markup from the document?") == QMessageBox.Yes:
            self.push_undo()
            self._delete_comments()
            self.mark_dirty()
            self.refresh_all()

    # tracked content (watermarks / headers & footers can be removed later)
    def _tracked(self, key):
        try:
            t, v = self.doc.xref_get_key(self.doc.pdf_catalog(), key)
            return [int(x) for x in v.strip("()").split(",") if x.strip()] if t == "string" else []
        except Exception:
            return []

    def _add_tracked(self, page, key, fn):
        page.wrap_contents()
        before = set(page.get_contents())
        fn()
        new = [x for x in page.get_contents() if x not in before]
        cur = self._tracked(key) + new
        self.doc.xref_set_key(self.doc.pdf_catalog(), key, pymupdf.get_pdf_str(",".join(map(str, cur))))

    def remove_tracked(self, key, what):
        if not self.doc:
            return
        ids = set(self._tracked(key))
        if not ids:
            info(self, f"No {what} created by Folio were found in this document.")
            return
        self.push_undo()
        for p in self.doc:
            cont = p.get_contents()
            keep = [x for x in cont if x not in ids]
            if len(keep) != len(cont):
                self.doc.xref_set_key(p.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in keep) + "]")
        self.doc.xref_set_key(self.doc.pdf_catalog(), key, "null")
        self.mark_dirty()
        self.refresh_all()

    def watermark(self):
        if not self.doc:
            return
        dlg = PD.WatermarkDialog(len(self.doc), self.view.cur_page, self)
        if not dlg.exec():
            return
        try:
            pages = dlg.scope.pages()
        except ValueError as e:
            error(self, e)
            return
        self.push_undo()
        text, fs = dlg.text.text(), dlg.size.value()
        c = dlg.color
        col = (c.redF(), c.greenF(), c.blueF())
        op = 1 - dlg.opacity.value() / 100
        rot = dlg.rot.value()
        pos = dlg.pos.currentText()
        behind = dlg.behind.currentIndex() == 1
        font = pymupdf.Font("helv")
        for i in pages:
            p = self.doc[i]
            r = p.rect
            tl = font.text_length(text, fs)
            cy = {"Center": r.height / 2, "Top": r.height * 0.15, "Bottom": r.height * 0.85}[pos]
            center = pymupdf.Point(r.width / 2, cy)
            if p.rotation:
                center = center * p.derotation_matrix
            start = pymupdf.Point(center.x - tl / 2, center.y + fs * 0.35)
            m = pymupdf.Matrix(-rot - p.rotation if p.rotation else -rot)

            def put(p=p, start=start, center=center, m=m):
                p.insert_text(start, text, fontsize=fs, fontname="helv", color=col, fill_opacity=op,
                              stroke_opacity=op, morph=(center, m), overlay=not behind)
            self._add_tracked(p, "FolioWM", put)
        self.mark_dirty()
        self.refresh_pages(pages)

    def header_footer(self):
        if not self.doc:
            return
        dlg = PD.HeaderFooterDialog(len(self.doc), self.view.cur_page, self)
        if not dlg.exec():
            return
        try:
            pages = dlg.scope.pages()
        except ValueError as e:
            error(self, e)
            return
        self.push_undo()
        fs, mg = dlg.size.value(), dlg.margin.value()
        font = pymupdf.Font("helv")
        fontfile = D.unicode_font_file()
        if fontfile:
            font = pymupdf.Font(fontfile=fontfile)
        total = len(pages)
        for n, i in enumerate(pages):
            p = self.doc[i]
            r = p.rect
            items = []
            for k, le in dlg.cells.items():
                if not le.text():
                    continue
                t = dlg.render(le.text(), n, total)
                tl = font.text_length(t, fs)
                x = {"l": mg, "c": (r.width - tl) / 2, "r": r.width - mg - tl}[k[1]]
                y = mg + fs if k[0] == "t" else r.height - mg
                pt = pymupdf.Point(x, y)
                if p.rotation:
                    pt = pt * p.derotation_matrix
                items.append((pt, t))

            def put(p=p, items=items):
                kw = {"fontname": "FolioHF", "fontfile": fontfile} if fontfile else {"fontname": "helv"}
                for pt, t in items:
                    p.insert_text(pt, t, fontsize=fs, rotate=p.rotation, color=(0, 0, 0), **kw)
            if items:
                self._add_tracked(p, "FolioHF", put)
        self.mark_dirty()
        self.refresh_pages(pages)

    # ------------------------------------------------------------------ recognition, export, sharing
    def recognize(self, pages=None):
        if not self.doc:
            return
        if not ocr.available():
            error(self, "The OCR engine (tesseract) was not found.")
            return
        dlg = PD.RecognizeDialog(self)
        if pages is None and not dlg.exec():
            return
        langs = dlg.lang.languages()
        only_missing = dlg.only_missing.isChecked() if pages is None else False
        orient = dlg.orient.isChecked() if pages is None else False
        data = self.doc.tobytes()
        sel = pages

        def work(progress, cancel):
            d = pymupdf.open("pdf", data)
            todo = sel if sel is not None else [i for i in range(len(d)) if not only_missing or len(d[i].get_text("words")) < 5]
            for n, i in enumerate(todo):
                if cancel():
                    raise RuntimeError("Cancelled")
                progress(n, len(todo), f"Recognizing page {i + 1} ({n + 1} of {len(todo)})...")
                if orient:
                    pix = d[i].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                    r = ocr.detect_orientation(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                    if r:
                        d[i].set_rotation((d[i].rotation + r) % 360)
                pipeline.add_ocr_layer(d, i, langs, dpi=settings()["ocr_dpi"])
            return d.tobytes(garbage=1), len(todo)

        def done(res):
            b, n = res
            if not n:
                info(self, "All pages already contain a text layer.")
                return
            self.push_undo()
            self.doc = pymupdf.open("pdf", b)
            self.mark_dirty()
            self.refresh_all()
            self.statusBar().showMessage(f"Recognized {n} page(s). The text is now searchable and selectable.", 6000)
        run_task(self, "Recognizing document...", work, on_done=done)

    def to_ocr_editor(self):
        if not self.doc:
            paths.launch_sibling("ocr")
            return
        paths.launch_sibling("ocr", self.path if (self.path and not self.dirty) else self._temp_copy())

    def compare(self):
        if self.doc:
            paths.launch_sibling("compare", self.path if (self.path and not self.dirty) else self._temp_copy())
        else:
            paths.launch_sibling("compare")

    def email(self):
        if self.doc:
            f = self._temp_copy()
            paths.run_external(["xdg-email", "--subject", Path(f).stem, "--attach", f])

    def print_doc(self):
        if not self.doc:
            return
        pr = QPrinter(QPrinter.HighResolution)
        pr.setFromTo(1, len(self.doc))
        dlg = QPrintDialog(pr, self)
        dlg.setMinMax(1, len(self.doc))
        if not dlg.exec():
            return
        a, b = (pr.fromPage(), pr.toPage()) if pr.fromPage() else (1, len(self.doc))
        dpi = min(300, pr.resolution())
        painter = QPainter(pr)
        for n, i in enumerate(range(a - 1, b)):
            if n:
                pr.newPage()
            p = self.doc[i]
            pix = p.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
            q = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            r = painter.viewport()
            s = q.size().scaled(r.size(), Qt.KeepAspectRatio)
            painter.drawImage(QRectF(0, 0, s.width(), s.height()), q)
        painter.end()

    def quick_help(self):
        info(self, "PDF Editor quick start:\n\n"
                   "• Select Objects (default): drag to select text; Ctrl+C copies. Right-click for markup options.\n"
                   "• PDF Tools bar: notes, highlight, drawing, text boxes, stamps, signatures, edit/erase content,\n"
                   "  links, pictures, crop, header & footer, watermark, redaction and password security.\n"
                   "• Pages pane: drag thumbnails to reorder; right-click to rotate, add, replace, extract or delete.\n"
                   "• Recognize Document adds an invisible OCR text layer to scanned pages.\n"
                   "• Save As converts to Word, Excel, ODT, EPUB and more.")


def run(argv, pdf_mode=False):
    app = make_app("reader", argv)
    files = [a for a in argv if not a.startswith("-") and Path(a).exists()]
    if files:
        for f in files:
            if f.endswith(D.PROJECT_EXT):
                paths.launch_sibling("ocr", f)
            else:
                PdfEditor.open_window(f)
        if not _windows:
            w = StartWindow()
            w.show()
            _windows.append(w)
    elif pdf_mode:
        PdfEditor.open_window(None)
    else:
        w = StartWindow()
        w.show()
        _windows.append(w)
    return app.exec()
