"""Folio OCR Editor — pages, image pane with areas, editable text pane, verification."""
import copy
import json
import os
import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QGuiApplication, QKeySequence, QPainter,
                           QPen, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextTableFormat)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox,
                               QSplitter, QTextEdit, QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import APPS
from ..core import document as D
from ..core import exporters, imageproc, ocr, paths
from ..core.ocr import build_table
from ..core.settings import settings
from ..ui.dialogs import OptionsDialog, ScanDialog, ask_page_range, format_menu, save_dialog
from ..ui.qtutil import (app_icon, ask, error, icon, info, make_app, pil_to_pixmap, pil_to_qimage, qimage_to_pil,
                         run_task, thumbnail_icon)
from ..ui.widgets import AREA_COLORS, AboutDialog, AreaItem, LanguageCombo, MultiPageView, ThumbnailList

WORD_PROP = QTextFormat.UserProperty + 1
AREA_TOOLS = [("text", "&Text Area", "area-text"), ("table", "Tabl&e Area", "area-table"),
              ("picture", "&Picture Area", "area-picture"), ("background", "Back&ground Picture Area", "area-background"),
              ("barcode", "Bar&code Area", "area-barcode"), ("recognition", "&Recognition Area", "area-recognition")]


class ImageEditPane(QScrollArea):
    """'Edit Page Images' pane."""

    def __init__(self, ed):
        super().__init__()
        self.ed = ed
        self.setWidgetResizable(True)
        w = QWidget()
        v = QVBoxLayout(w)
        self.setWidget(w)
        self.setMinimumWidth(270)

        def group(title):
            g = QGroupBox(title)
            l = QVBoxLayout(g)
            v.addWidget(g)
            return l

        l = group("Recommended preprocessing")
        b = QPushButton("Apply recommended preprocessing")
        b.setToolTip("Correct orientation, deskew, fix resolution and contrast")
        b.clicked.connect(lambda: self.apply("Recommended preprocessing", lambda im: imageproc.autocontrast(
            imageproc.deskew(imageproc.rotate(im, ocr.detect_orientation(im))))))
        l.addWidget(b)

        l = group("Deskew")
        b = QPushButton(icon("deskew"), "Deskew")
        b.clicked.connect(lambda: self.apply("Deskew", imageproc.deskew))
        l.addWidget(b)

        l = group("Rotate && Flip")
        h = QHBoxLayout()
        for ic, tip, fn in (("rotate-left", "Rotate Left", lambda im: imageproc.rotate(im, 270)),
                            ("rotate-right", "Rotate Right", lambda im: imageproc.rotate(im, 90)),
                            (None, "180°", lambda im: imageproc.rotate(im, 180)),
                            (None, "Flip ↔", lambda im: imageproc.flip(im, True)),
                            (None, "Flip ↕", lambda im: imageproc.flip(im, False))):
            bt = QToolButton()
            if ic:
                bt.setIcon(icon(ic))
            else:
                bt.setText(tip)
            bt.setToolTip(tip)
            bt.clicked.connect(lambda _=False, fn=fn, tip=tip: self.apply(tip, fn))
            h.addWidget(bt)
        l.addLayout(h)
        h = QHBoxLayout()
        self.free = QDoubleSpinBox(minimum=-45, maximum=45, singleStep=0.5, suffix="°")
        b = QPushButton("Rotate")
        b.clicked.connect(lambda: self.apply("Rotate", lambda im: imageproc.rotate_free(im, self.free.value())))
        h.addWidget(self.free)
        h.addWidget(b)
        l.addLayout(h)

        l = group("Split")
        h = QHBoxLayout()
        for t, vert in (("Split vertically", True), ("Split horizontally", False)):
            b = QPushButton(t)
            b.clicked.connect(lambda _=False, vert=vert: self.ed.split_pages(vert))
            h.addWidget(b)
        l.addLayout(h)

        l = group("Crop")
        l.addWidget(QLabel("Draw a rectangle on the image, then:"))
        h = QHBoxLayout()
        b = QPushButton(icon("crop"), "Select area")
        b.clicked.connect(lambda: self.ed.set_tool("crop"))
        b2 = QPushButton("Crop")
        b2.clicked.connect(self.ed.apply_crop)
        h.addWidget(b)
        h.addWidget(b2)
        l.addLayout(h)

        l = group("Brightness && Contrast")
        f = QFormLayout()
        self.br = QSlider(Qt.Horizontal, minimum=-100, maximum=100)
        self.ct = QSlider(Qt.Horizontal, minimum=-100, maximum=100)
        f.addRow("Brightness:", self.br)
        f.addRow("Contrast:", self.ct)
        l.addLayout(f)
        b = QPushButton("Apply")
        b.clicked.connect(lambda: self.apply("Brightness and contrast", lambda im: imageproc.brightness_contrast(
            im, self.br.value(), self.ct.value())))
        l.addWidget(b)

        l = group("Levels")
        f = QFormLayout()
        self.lb = QSpinBox(minimum=0, maximum=254, value=0)
        self.lw = QSpinBox(minimum=1, maximum=255, value=255)
        self.lg = QDoubleSpinBox(minimum=0.1, maximum=5, value=1.0, singleStep=0.1)
        f.addRow("Input black:", self.lb)
        f.addRow("Input white:", self.lw)
        f.addRow("Gamma:", self.lg)
        l.addLayout(f)
        b = QPushButton("Apply")
        b.clicked.connect(lambda: self.apply("Levels", lambda im: imageproc.levels(im, self.lb.value(), self.lw.value(), self.lg.value())))
        l.addWidget(b)

        l = group("Other")
        for t, fn in (("Invert", imageproc.invert), ("Photo correction", imageproc.photo_correction),
                      ("Remove color marks", imageproc.remove_color_marks), ("Remove noise", imageproc.denoise),
                      ("Convert to black-and-white", imageproc.binarize), ("Convert to grayscale", imageproc.gray)):
            b = QPushButton(t)
            b.clicked.connect(lambda _=False, fn=fn, t=t: self.apply(t, fn))
            l.addWidget(b)
        h = QHBoxLayout()
        b = QPushButton(icon("erase"), "Eraser")
        b.setToolTip("Draw rectangles on the image to erase them")
        b.setCheckable(True)
        b.toggled.connect(lambda on: self.ed.set_tool("erase" if on else "select"))
        self.eraser_btn = b
        h.addWidget(b)
        l.addLayout(h)
        h = QHBoxLayout()
        self.dpi = QSpinBox(minimum=50, maximum=1200, value=300, suffix=" dpi")
        b = QPushButton("Correct resolution")
        b.clicked.connect(lambda: self.apply("Resolution", lambda im: imageproc.set_resolution(im, self.dpi.value())))
        h.addWidget(self.dpi)
        h.addWidget(b)
        l.addLayout(h)

        l = group("Apply to")
        self.scope = QComboBox()
        self.scope.addItems(["Current page", "Selected pages", "All pages"])
        l.addWidget(self.scope)
        v.addStretch()

    def apply(self, title, fn):
        self.ed.apply_image_op(title, fn, self.scope.currentIndex())


class VerifyDialog(QDialog):
    """Verification: step through uncertain words, confirm or correct them."""

    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed
        self.setWindowTitle("Verification")
        self.resize(640, 330)
        v = QVBoxLayout(self)
        self.img = QLabel()
        self.img.setMinimumHeight(110)
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setStyleSheet("background:white;border:1px solid #bbb")
        v.addWidget(self.img)
        self.diag = QLabel()
        v.addWidget(self.diag)
        h = QHBoxLayout()
        self.edit = QLineEdit()
        f = self.edit.font()
        f.setPointSize(f.pointSize() + 4)
        self.edit.setFont(f)
        h.addWidget(self.edit, 1)
        v.addLayout(h)
        h = QHBoxLayout()
        for t, fn in (("&Confirm", self.confirm), ("Confirm &All", self.confirm_all), ("A&dd to Dictionary", self.add_dict),
                      ("&Replace", self.replace), ("&Skip", self.skip)):
            b = QPushButton(t)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch()
        b = QPushButton("Close")
        b.clicked.connect(self.close)
        h.addWidget(b)
        v.addLayout(h)
        self.edit.returnPressed.connect(self.replace)
        self.cur = None
        self.next()

    def next(self):
        self.cur = self.ed.find_uncertain(self.cur, 1)
        if not self.cur:
            self.img.clear()
            self.diag.setText("<b>Verification complete.</b> No more uncertain words.")
            self.edit.clear()
            return
        pi, w = self.cur
        self.ed.go_to_page(pi)
        self.ed.show_word(pi, w)
        img = self.ed.project.image(self.ed.project.pages[pi])
        x, y, ww, hh = w.bbox
        pad = max(10, hh)
        crop = img.crop((max(0, x - pad * 3), max(0, y - pad), min(img.width, x + ww + pad * 3), min(img.height, y + hh + pad)))
        pm = pil_to_pixmap(crop)
        self.img.setPixmap(pm.scaled(self.img.width() - 10, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.diag.setText(f"Uncertain word (confidence {w.conf:.0f}%) on page {pi + 1}:")
        self.edit.setText(w.text)
        self.edit.selectAll()
        self.edit.setFocus()

    def confirm(self):
        if self.cur:
            self.cur[1].conf = 100.0
            self.ed.word_changed(self.cur[0])
        self.next()

    def confirm_all(self):
        if self.cur:
            t = self.cur[1].text
            for p in self.ed.project.pages:
                if p.ocr:
                    for w in p.ocr.words():
                        if w.text == t:
                            w.conf = 100.0
            self.ed.word_changed(self.cur[0])
        self.next()

    def add_dict(self):
        if self.cur:
            s = settings()
            words = set(s["user_words"])
            words.add(self.cur[1].text)
            s["user_words"] = sorted(words)
        self.confirm_all()

    def replace(self):
        if self.cur:
            self.cur[1].text = self.edit.text().strip() or self.cur[1].text
            self.cur[1].conf = 100.0
            self.ed.word_changed(self.cur[0])
        self.next()

    def skip(self):
        self.next()


class OcrEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(app_icon("ocr"))
        self.resize(1440, 900)
        self.project = D.Project()
        self.cur = -1
        self.tool = "select"
        self.undo_stack = []
        self.redo_stack = []
        self._building_text = False
        self._crop_rect = None
        self.area_items = []
        self.selected_area = None
        self._build_ui()
        self._build_menus()
        self._update_title()
        self.statusBar().showMessage("Open images or PDF files to start (File ▸ Open Image...).")

    # ================================================================== UI
    def _build_ui(self):
        tb = QToolBar("Main Toolbar")
        tb.setObjectName("main")
        tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        tb.addAction(icon("pdf-editor"), "New Task", lambda: paths.launch_sibling("reader"))
        tb.addAction(icon("document-open", "document-open"), "Open", self.open_images)
        tb.addAction(icon("scan"), "Scan", self.scan_pages)
        tb.addSeparator()
        tb.addAction(icon("recognize"), "Recognize", self.recognize_all)
        tb.addAction(icon("verify"), "Verify", self.verify)
        tb.addSeparator()
        self.save_btn = QToolButton(text="Save", icon=icon("document-save"))
        self.save_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.save_btn.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(self)
        format_menu(m, self.save_as)
        m.addSeparator()
        m.addAction("P&age Images...", self.save_page_images)
        self.save_btn.setMenu(m)
        self.save_btn.clicked.connect(lambda: self.save_as(self.last_format if hasattr(self, "last_format") else "docx"))
        tb.addWidget(self.save_btn)
        self.send_btn = QToolButton(text="Send To", icon=icon("send"))
        self.send_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.send_btn.setPopupMode(QToolButton.InstantPopup)
        self.send_menu = QMenu(self)
        self._fill_send(self.send_menu)
        self.send_btn.setMenu(self.send_menu)
        tb.addWidget(self.send_btn)
        tb.addSeparator()
        tb.addWidget(QLabel(" Languages: "))
        self.lang = LanguageCombo()
        tb.addWidget(self.lang)
        tb.addSeparator()
        tb.addAction(icon("preferences-system", "configure", "document-properties"), "Options", self.options)

        # pages pane
        self.thumbs = ThumbnailList(size=110)
        self.thumbs.currentRowChanged.connect(self.go_to_page)
        self.thumbs.moved.connect(self._pages_moved)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        self.pages_dock = QDockWidget("Pages", self)
        self.pages_dock.setObjectName("pages")
        self.pages_dock.setWidget(self.thumbs)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.pages_dock)

        # image pane
        img_w = QWidget()
        iv = QVBoxLayout(img_w)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(0)
        itb = QToolBar()
        itb.setIconSize(QSize(20, 20))
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for key, tip, ic in [("select", "Select Objects", "tool-select"), ("hand", "Hand Tool", "tool-hand")] + \
                [(k, "Draw " + t.replace("&", ""), ic) for k, t, ic in AREA_TOOLS]:
            a = itb.addAction(icon(ic), tip)
            a.setCheckable(True)
            a.setActionGroup(self.tool_group)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_actions[key] = a
            if key == "hand":
                itb.addSeparator()
        self.tool_actions["select"].setChecked(True)
        itb.addSeparator()
        itb.addAction(icon("recognize"), "Recognize Page", self.recognize_page)
        itb.addAction(icon("analyze"), "Analyze Page", self.analyze_page)
        itb.addAction(icon("image-edit"), "Edit Page Images", lambda: self.img_dock.setVisible(not self.img_dock.isVisible()))
        itb.addSeparator()
        itb.addAction(icon("zoom-in"), "Zoom In", lambda: self.view.zoom_in())
        itb.addAction(icon("zoom-out"), "Zoom Out", lambda: self.view.zoom_out())
        itb.addAction(icon("fit-page"), "Best Fit", lambda: self.view.fit_page())
        itb.addAction(icon("fit-width"), "Fit to Width", lambda: self.view.fit_width())
        iv.addWidget(itb)
        self.view = MultiPageView()
        self.view.rectDrawn.connect(self._rect_drawn)
        self.view.clicked.connect(self._image_clicked)
        self.view.contextRequested.connect(self._image_menu)
        self.view.zoomChanged.connect(lambda _: QTimer.singleShot(0, self._draw_areas))
        iv.addWidget(self.view, 1)

        # text pane
        txt_w = QWidget()
        tv = QVBoxLayout(txt_w)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        ttb = QToolBar()
        ttb.setIconSize(QSize(20, 20))
        ttb.addAction(icon("verify"), "Verify Text", self.verify)
        ttb.addAction(icon("go-previous"), "Previous Error", lambda: self.goto_error(-1))
        ttb.addAction(icon("go-next"), "Next Error", lambda: self.goto_error(1))
        ttb.addSeparator()
        self.font_size = QSpinBox(minimum=7, maximum=40, value=12, suffix=" pt")
        self.font_size.valueChanged.connect(lambda v: self.text.setFont(QFont("DejaVu Sans", v)))
        ttb.addWidget(QLabel(" Text size: "))
        ttb.addWidget(self.font_size)
        self.page_state = QLabel("")
        ttb.addWidget(QWidget())
        ttb.addWidget(self.page_state)
        tv.addWidget(ttb)
        self.text = QTextEdit()
        self.text.setFont(QFont("DejaVu Sans", 12))
        self.text.setAcceptRichText(False)
        self.text.cursorPositionChanged.connect(self._text_cursor)
        self.text.textChanged.connect(self._text_changed)
        self._text_timer = QTimer(self, singleShot=True, interval=600, timeout=self._commit_text)
        tv.addWidget(self.text, 1)

        self.split = QSplitter()
        self.split.addWidget(img_w)
        self.split.addWidget(txt_w)
        self.split.setSizes([700, 600])
        self.setCentralWidget(self.split)

        # zoom pane
        self.zoom_lbl = QLabel()
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        self.zoom_lbl.setMinimumHeight(90)
        self.zoom_lbl.setStyleSheet("background:white")
        self.zoom_dock = QDockWidget("Zoom", self)
        self.zoom_dock.setObjectName("zoom")
        self.zoom_dock.setWidget(self.zoom_lbl)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.zoom_dock)

        # image editor pane
        self.img_edit = ImageEditPane(self)
        self.img_dock = QDockWidget("Edit Page Images", self)
        self.img_dock.setObjectName("imgedit")
        self.img_dock.setWidget(self.img_edit)
        self.addDockWidget(Qt.RightDockWidgetArea, self.img_dock)
        self.img_dock.hide()
        self.setAcceptDrops(True)

    def _fill_send(self, m):
        m.clear()
        m.addAction(icon("pdf-editor"), "PDF &Editor...", self.send_to_pdf_editor)
        m.addSeparator()
        m.addAction("Word Processor (&Writer)", lambda: self.send_to("odt"))
        m.addAction("Microsoft &Word document", lambda: self.send_to("docx"))
        m.addAction("&Spreadsheet (Calc / Excel)", lambda: self.send_to("xlsx"))
        m.addAction("&Presentation (Impress / PowerPoint)", lambda: self.send_to("pptx"))
        m.addAction("Web Brow&ser", lambda: self.send_to("html"))
        m.addAction("C&lipboard", self.send_clipboard)
        m.addAction("E-book reader (EPUB)...", lambda: self.send_to("epub"))

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("New &Task...", lambda: paths.launch_sibling("reader"), "Ctrl+N")
        f.addSeparator()
        f.addAction(icon("document-open"), "&Open Image...", self.open_images, QKeySequence.Open)
        f.addAction(icon("scan"), "&Scan Pages...", self.scan_pages, "Ctrl+K")
        f.addSeparator()
        sa = f.addMenu("Save &As")
        format_menu(sa, self.save_as)
        sa.addSeparator()
        sa.addAction("P&age Images...", self.save_page_images)
        st = f.addMenu("S&end To")
        self._fill_send(st)
        f.addAction("&Close", self.close_document)
        f.addSeparator()
        f.addAction("Ne&w OCR Project", self.new_project, "Ctrl+Shift+N")
        f.addAction("Open OCR Pro&ject...", self.open_project_dialog)
        f.addAction("Sa&ve OCR Project...", self.save_project, QKeySequence.Save)
        f.addAction("Save OCR Project &As...", lambda: self.save_project(True))
        f.addSeparator()
        em = f.addMenu("E-&mail")
        em.addAction("&Document...", lambda: self.email("docx"))
        em.addAction("&Images...", lambda: self.email("images"))
        pm = f.addMenu("&Print")
        pm.addAction("&Text...", self.print_text, QKeySequence.Print)
        pm.addAction("I&mage...", self.print_images)
        f.addSeparator()
        self.recent_menu = f.addMenu("Recent OCR Projects")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        f.addAction("E&xit", self.close, QKeySequence.Quit)

        e = mb.addMenu("&Edit")
        e.addAction(icon("undo"), "&Undo", self.undo, QKeySequence.Undo)
        e.addAction(icon("redo"), "&Redo", self.redo, QKeySequence.Redo)
        e.addSeparator()
        e.addAction("&Cut", self.text.cut)
        e.addAction("Cop&y", self.copy)
        e.addAction("Co&py Area as Image", self.copy_area_image)
        e.addAction("P&aste", self.text.paste)
        e.addAction("&Delete", self.delete_selection)
        e.addSeparator()
        e.addAction("&Select All", self.text.selectAll)
        e.addAction("Ca&ncel Selection", lambda: (self.text.moveCursor(QTextCursor.Start), self.view.scene().clearSelection()))
        e.addSeparator()
        e.addAction(icon("page-delete"), "D&elete Pages...", self.delete_pages)
        e.addAction("De&lete Areas and Text", self.delete_areas_and_text)
        e.addAction("Delete Te&xt", self.delete_text)
        e.addSeparator()
        sel = e.addMenu("Selec&t")
        sel.addAction("&All Pages", self.thumbs.selectAll)
        sel.addAction("&Even Pages", lambda: self._select_pages(1))
        sel.addAction("&Odd Pages", lambda: self._select_pages(0))
        e.addAction("Reorder Pages&...", self.reorder_dialog)
        e.addSeparator()
        rp = e.addMenu("R&otate Pages")
        rp.addAction(icon("rotate-right"), "&Rotate Right", lambda: self.apply_image_op("Rotate", lambda im: imageproc.rotate(im, 90), 1), "Ctrl+Shift+>")
        rp.addAction(icon("rotate-left"), "R&otate Left", lambda: self.apply_image_op("Rotate", lambda im: imageproc.rotate(im, 270), 1), "Ctrl+Shift+<")
        rp.addAction("Ro&tate 180°", lambda: self.apply_image_op("Rotate", lambda im: imageproc.rotate(im, 180), 1))
        rp.addSeparator()
        rp.addAction("&Flip Vertically", lambda: self.apply_image_op("Flip", lambda im: imageproc.flip(im, False), 1))
        rp.addAction("Flip &Horizontally", lambda: self.apply_image_op("Flip", lambda im: imageproc.flip(im, True), 1))
        e.addAction(icon("crop"), "Crop Pages...", lambda: (self.img_dock.show(), self.set_tool("crop")))
        e.addAction("Split Pa&ges...", lambda: self.split_pages(True))
        e.addAction(icon("image-edit"), "Edit Page I&mages...", lambda: self.img_dock.show(), "Ctrl+I")
        e.addSeparator()
        e.addAction(icon("search"), "&Find...", self.find, QKeySequence.Find)
        e.addAction("F&ind Next", self.find_next, QKeySequence.FindNext)
        e.addAction("Replace...", self.replace, QKeySequence.Replace)
        e.addAction("Insert Sym&bol...", self.insert_symbol)

        v = mb.addMenu("&View")
        nav = v.addMenu("Naviga&tion Pane")
        nav.addAction(self.pages_dock.toggleViewAction())
        zp = v.addMenu("&Zoom Pane")
        zp.addAction(self.zoom_dock.toggleViewAction())
        v.addSeparator()
        v.addAction("&Image Pane Only", lambda: self.split.setSizes([1, 0]), "F5")
        v.addAction("I&mage and Text Panes", lambda: self.split.setSizes([700, 600]), "F6")
        v.addAction("T&ext Pane Only", lambda: self.split.setSizes([0, 1]), "F7")
        v.addSeparator()
        v.addAction(icon("zoom-in"), "Z&oom In", self.view.zoom_in, QKeySequence.ZoomIn)
        v.addAction(icon("zoom-out"), "Zoom O&ut", self.view.zoom_out, QKeySequence.ZoomOut)
        v.addAction(icon("fit-page"), "Be&st Fit", self.view.fit_page)
        v.addAction(icon("fit-width"), "&Fit to Width", self.view.fit_width)
        v.addSeparator()
        gp = v.addMenu("&Go to Page")
        gp.addAction("&Page Number...", self.goto_page_number, "Ctrl+G")
        gp.addSeparator()
        gp.addAction("N&ext Page", lambda: self.go_to_page(self.cur + 1), "Ctrl+Down")
        gp.addAction("P&revious Page", lambda: self.go_to_page(self.cur - 1), "Ctrl+Up")
        gp.addAction("&First Page", lambda: self.go_to_page(0), "Ctrl+Home")
        gp.addAction("&Last Page", lambda: self.go_to_page(len(self.project.pages) - 1), "Ctrl+End")
        v.addAction("P&age Properties...", self.page_properties)

        r = mb.addMenu("&Recognize")
        r.addAction(icon("recognize"), "&Recognize All Pages", self.recognize_all, "Ctrl+Shift+R")
        r.addAction("R&ecognize Page", self.recognize_page, "Ctrl+R")
        r.addSeparator()
        r.addAction(icon("analyze"), "&Analyze All Pages", self.analyze_all, "Ctrl+Shift+E")
        r.addAction("Ana&lyze Page", self.analyze_page, "Ctrl+E")
        r.addSeparator()
        r.addAction(icon("verify"), "&Verify Text...", self.verify, "Ctrl+F7")
        r.addAction("Ne&xt Error", lambda: self.goto_error(1), "Alt+Down")
        r.addAction("&Previous Error", lambda: self.goto_error(-1), "Alt+Up")
        r.addAction("&Mark Text as Verified", self.mark_verified)

        a = mb.addMenu("&Area")
        a.addAction("&Recognize Area", self.recognize_area)
        a.addAction("&Analyze Area", self.analyze_area)
        a.addSeparator()
        ct = a.addMenu("&Change Area Type")
        for k, t, ic in AREA_TOOLS:
            ct.addAction(icon(ic), t, lambda k=k: self.change_area_type(k))
        da = a.addMenu("&Draw Area")
        for k, t, ic in AREA_TOOLS:
            da.addAction(icon(ic), t, lambda k=k: self.set_tool(k))
        a.addAction("D&elete Areas", self.delete_areas)
        a.addAction("Re&order Areas", self.reorder_areas)
        a.addSeparator()
        a.addAction(icon("tool-select"), "&Select Objects", lambda: self.set_tool("select"))
        a.addAction(icon("tool-hand"), "&Hand Tool", lambda: self.set_tool("hand"))
        a.addSeparator()
        a.addAction("Anal&yze Table Structure", self.analyze_table)
        a.addSeparator()
        a.addAction("Sa&ve Area Template...", self.save_area_template)
        a.addAction("&Load Area Template...", self.load_area_template)

        t = mb.addMenu("&Tools")
        t.addAction("&View Dictionaries...", self.dictionaries)
        t.addSeparator()
        t.addAction(icon("redact"), "&Redaction Mode", lambda: self.set_tool("redact"))
        t.addSeparator()
        t.addAction("&Hot Folder...", lambda: HotFolderDialog(self).exec())
        t.addAction(icon("compare"), "Compare &Documents", lambda: paths.launch_sibling("compare"))
        t.addAction(icon("capture"), "Folio Sna&p", lambda: paths.launch_sibling("snap"))
        t.addSeparator()
        t.addAction("Opt&ions...", self.options, "Ctrl+Shift+O")

        h = mb.addMenu("&Help")
        h.addAction("&Keyboard and Tips", self.help)
        h.addAction("Ab&out", lambda: AboutDialog("ocr", self).exec())

    # ================================================================== title/state
    def _update_title(self):
        name = Path(self.project.path).stem if self.project.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.project.dirty else ''} — {APPS['ocr'][0]}")

    def mark_dirty(self):
        self.project.dirty = True
        self._update_title()

    def page(self):
        if 0 <= self.cur < len(self.project.pages):
            return self.project.pages[self.cur]
        return None

    # ================================================================== loading pages
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        projects = [f for f in files if f.endswith(D.PROJECT_EXT)]
        if projects:
            self.open_project(projects[0])
        elif files:
            self.add_files(files)

    def open_images(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Open Image", str(Path.home()), D.OPEN_FILTER)
        if fns:
            self.add_files(fns)

    def add_files(self, files, recognize=None):
        jobs = []
        for f in files:
            if f.endswith(D.PROJECT_EXT):
                self.open_project(f)
                return
            cnt = D.page_count(f)
            idx = None
            if cnt > 20:
                spec = ask_page_range(self, cnt)
                if spec is None:
                    continue
                try:
                    idx = D.parse_page_range(spec, cnt)
                except ValueError as e:
                    error(self, e)
                    continue
            jobs.append((f, idx))
        if not jobs:
            return
        pre = settings()["preprocess"]
        mode = settings()["pdf_mode"]
        dpi = settings()["ocr_dpi"]

        def work(progress, cancel):
            out = []
            n = 0
            for f, idx in jobs:
                for lp in D.load_pages(f, dpi=dpi, page_indices=idx):
                    if cancel():
                        return out
                    progress(n, n + 1, f"Opening pages... {Path(f).name} ({lp.index + 1})")
                    n += 1
                    use_text = lp.text_layer is not None and mode != "ocr" and (
                        mode == "text" or sum(1 for _ in lp.text_layer.words()) >= 5)
                    if use_text:
                        out.append((lp.image, f"{Path(f).name} #{lp.index + 1}", lp.text_layer))
                    else:
                        for im in imageproc.preprocess(lp.image, pre):
                            out.append((im, f"{Path(f).name} #{lp.index + 1}", None))
            return out

        def done(items):
            first = len(self.project.pages)
            for img, name, tl in items:
                self.project.add_image(img, name, tl)
            for f, _ in jobs:
                settings().add_recent("recent_documents", f)
            self.refresh_thumbs()
            self.go_to_page(first)
            self.mark_dirty()
            todo = [p for p in self.project.pages[first:] if not p.recognized]
            if todo and (recognize if recognize is not None else True):
                self.recognize_pages(todo)
        run_task(self, "Opening file...", work, on_done=done)

    def scan_pages(self):
        dlg = ScanDialog(self)
        if dlg.exec() and dlg.images:
            first = len(self.project.pages)
            for i, img in enumerate(dlg.images):
                for im in imageproc.preprocess(img, settings()["preprocess"]):
                    self.project.add_image(im, f"Scanned page {first + i + 1}")
            self.refresh_thumbs()
            self.go_to_page(first)
            self.mark_dirty()
            self.recognize_pages(self.project.pages[first:])

    # ================================================================== thumbnails / navigation
    def refresh_thumbs(self, keep=True):
        cur = self.cur
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i, p in enumerate(self.project.pages):
            state = "✓" if p.verified else ("" if p.recognized else "•")
            it = QListWidgetItem(thumbnail_icon(self.project.image(p), 110), f"{i + 1} {state}")
            it.setData(Qt.UserRole, i)
            it.setToolTip(f"{p.name}\n{'Recognized' if p.recognized else 'Not recognized'}"
                          f"{' · verified' if p.verified else ''}")
            self.thumbs.addItem(it)
        self.thumbs.blockSignals(False)
        if keep and 0 <= cur < self.thumbs.count():
            self.thumbs.setCurrentRow(cur)

    def _update_thumb(self, i):
        if 0 <= i < self.thumbs.count():
            p = self.project.pages[i]
            state = "✓" if p.verified else ("" if p.recognized else "•")
            it = self.thumbs.item(i)
            it.setIcon(thumbnail_icon(self.project.image(p), 110))
            it.setText(f"{i + 1} {state}")

    def _pages_moved(self, order):
        self.project.pages = [self.project.pages[i] for i in order]
        cur_id = self.page().id if self.page() else None
        self.refresh_thumbs(False)
        self.cur = next((i for i, p in enumerate(self.project.pages) if p.id == cur_id), 0)
        self.thumbs.setCurrentRow(self.cur)
        self.mark_dirty()

    def go_to_page(self, i):
        if not self.project.pages:
            self.cur = -1
            self.view.clear()
            self._building_text = True
            self.text.clear()
            self._building_text = False
            return
        i = max(0, min(i, len(self.project.pages) - 1))
        self._commit_text()
        self.cur = i
        if self.thumbs.currentRow() != i:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(i)
            self.thumbs.blockSignals(False)
        p = self.page()
        img = self.project.image(p)
        self.view.clear_overlays("word")
        self.view.clear_overlays("crop")
        self._crop_rect = None
        self.view.set_document([(img.width, img.height)], lambda _i, s, img=img: pil_to_qimage(
            img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR) if s < 1 else img))
        QTimer.singleShot(0, self.view.fit_width)
        self._draw_areas()
        self.show_text()
        self.statusBar().showMessage(f"Page {i + 1} of {len(self.project.pages)} — {p.name}")

    def _select_pages(self, parity):
        self.thumbs.clearSelection()
        for i in range(self.thumbs.count()):
            if i % 2 == parity:
                self.thumbs.item(i).setSelected(True)

    def selected_pages(self):
        idx = self.thumbs.selected_indices()
        return [self.project.pages[i] for i in idx] if idx else ([self.page()] if self.page() else [])

    def goto_page_number(self):
        n, ok = QInputDialog.getInt(self, "Go to Page", "Page number:", self.cur + 1, 1, max(1, len(self.project.pages)))
        if ok:
            self.go_to_page(n - 1)

    def _thumb_menu(self, pos):
        m = QMenu(self)
        m.addAction("&Recognize Page(s)", lambda: self.recognize_pages(self.selected_pages()))
        m.addAction("&Analyze Page(s)", lambda: self.analyze_pages(self.selected_pages()))
        m.addSeparator()
        m.addAction(icon("rotate-right"), "Rotate &Right", lambda: self.apply_image_op("Rotate", lambda im: imageproc.rotate(im, 90), 1))
        m.addAction(icon("rotate-left"), "Rotate &Left", lambda: self.apply_image_op("Rotate", lambda im: imageproc.rotate(im, 270), 1))
        m.addSeparator()
        m.addAction("Save Page &Images...", self.save_page_images)
        m.addAction(icon("page-delete"), "&Delete Pages", self.delete_pages)
        m.addAction("P&roperties...", self.page_properties)
        m.exec(self.thumbs.mapToGlobal(pos))

    # ================================================================== areas
    def set_tool(self, tool):
        self.tool = tool
        view_tool = {"select": "select", "hand": "hand"}.get(tool, "draw")
        self.view.set_tool(view_tool)
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        else:
            for a in self.tool_actions.values():
                a.setChecked(False)
        if tool != "erase" and self.img_edit.eraser_btn.isChecked():
            self.img_edit.eraser_btn.blockSignals(True)
            self.img_edit.eraser_btn.setChecked(False)
            self.img_edit.eraser_btn.blockSignals(False)
        hints = {"crop": "Draw the crop rectangle, then press Crop.", "erase": "Draw rectangles to erase.",
                 "redact": "Redaction mode: draw rectangles over content to black it out."}
        if tool in hints:
            self.statusBar().showMessage(hints[tool])

    def _draw_areas(self):
        sc = self.view.scene()
        for it in self.area_items:
            if it.scene():
                sc.removeItem(it)
        self.area_items = []
        p = self.page()
        if not p or not self.view.items:
            return
        for n, a in enumerate(p.areas):
            r = self.view.unit_rect_to_scene(0, QRectF(*a.rect))
            it = AreaItem(self.view, 0, n, a.kind, r, self._area_changed, self._area_selected, number=n + 1)
            sc.addItem(it)
            self.area_items.append(it)
        if self._crop_rect:
            self.view.set_overlays("crop", [(0, self._crop_rect)], pen=QPen(QColor(220, 40, 40), 2, Qt.DashLine),
                                   brush=QBrush(Qt.NoBrush), z=15)

    def _area_changed(self, idx, rect):
        p = self.page()
        if p and idx < len(p.areas):
            p.areas[idx].rect = (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
            self.mark_dirty()

    def _area_selected(self, idx):
        self.selected_area = idx

    def _rect_drawn(self, page, r: QRectF):
        p = self.page()
        if not p:
            return
        rect = (int(r.x()), int(r.y()), int(r.width()), int(r.height()))
        if r.width() < 5 or r.height() < 5:
            return
        if self.tool in [k for k, _, _ in AREA_TOOLS]:
            self.push_undo("Draw area")
            p.areas.append(D.Area(self.tool, rect))
            self.selected_area = len(p.areas) - 1
            self._draw_areas()
            self.mark_dirty()
        elif self.tool == "crop":
            self._crop_rect = r
            self._draw_areas()
        elif self.tool == "erase":
            self.push_undo("Erase")
            img = imageproc.erase(self.project.image(p), rect)
            self._replace_image(p, img, keep_ocr=True)
            self._drop_words(p, rect)
        elif self.tool == "redact":
            self.push_undo("Redact")
            img = imageproc.erase(self.project.image(p), rect, color=0 if self.project.image(p).mode == "L" else (0, 0, 0))
            self._replace_image(p, img, keep_ocr=True)
            self._drop_words(p, rect)
        elif self.tool == "select":
            # select areas intersecting the band
            for it in self.area_items:
                a = p.areas[it.index]
                it.setSelected(QRectF(*a.rect).intersects(r))

    def _drop_words(self, p, rect):
        if not p.ocr:
            return
        for b in p.ocr.blocks:
            for l in b.lines:
                l.words = [w for w in l.words if not D.area_contains(rect, w.bbox, 0.5)]
            b.lines = [l for l in b.lines if l.words]
        self.show_text()

    def _image_clicked(self, page, pt):
        p = self.page()
        if not p or not p.ocr or self.tool != "select":
            return
        for bi, b in enumerate(p.ocr.blocks):
            for li, l in enumerate(b.lines):
                for wi, w in enumerate(l.words):
                    x, y, ww, hh = w.bbox
                    if x <= pt.x() <= x + ww and y <= pt.y() <= y + hh:
                        self._select_word_in_text(bi, li, wi)
                        return

    def _image_menu(self, page, pt, gpos):
        p = self.page()
        if not p:
            return
        hit = None
        for i, a in enumerate(p.areas):
            if QRectF(*a.rect).contains(pt):
                hit = i
        if hit is not None:
            self.selected_area = hit
            for it in self.area_items:
                it.setSelected(it.index == hit)
        m = QMenu(self)
        if hit is not None:
            m.addAction(icon("recognize"), "&Recognize Area", self.recognize_area)
            m.addAction(icon("analyze"), "&Analyze Area", self.analyze_area)
            ct = m.addMenu("&Change Area Type")
            for k, t, ic in AREA_TOOLS:
                ct.addAction(icon(ic), t, lambda k=k: self.change_area_type(k))
            if p.areas[hit].kind == "table":
                m.addAction("Anal&yze Table Structure", self.analyze_table)
            m.addAction("Copy Area as &Image", self.copy_area_image)
            m.addAction("&Delete Area", self.delete_areas)
            m.addSeparator()
        m.addAction(icon("recognize"), "Recognize &Page", self.recognize_page)
        m.addAction(icon("analyze"), "Analyze P&age", self.analyze_page)
        m.addSeparator()
        m.addAction("Delete &All Areas and Text", self.delete_areas_and_text)
        m.exec(gpos)

    def _selected_area_indices(self):
        sel = [it.index for it in self.area_items if it.isSelected()]
        if not sel and self.selected_area is not None and self.page() and self.selected_area < len(self.page().areas):
            sel = [self.selected_area]
        return sel

    def change_area_type(self, kind):
        p = self.page()
        for i in self._selected_area_indices():
            p.areas[i].kind = kind
        self._draw_areas()
        self.mark_dirty()

    def delete_areas(self):
        p = self.page()
        if not p:
            return
        sel = set(self._selected_area_indices())
        if not sel:
            return
        self.push_undo("Delete areas")
        p.areas = [a for i, a in enumerate(p.areas) if i not in sel]
        self.selected_area = None
        self._draw_areas()
        self.mark_dirty()

    def reorder_areas(self):
        p = self.page()
        if p:
            p.areas.sort(key=lambda a: (round(a.rect[1] / 40), a.rect[0]))
            self._draw_areas()
            self.mark_dirty()

    def delete_areas_and_text(self):
        p = self.page()
        if p:
            self.push_undo("Delete areas and text")
            p.areas, p.ocr, p.text_override, p.analyzed = [], None, None, False
            self._draw_areas()
            self.show_text()
            self._update_thumb(self.cur)
            self.mark_dirty()

    def delete_text(self):
        p = self.page()
        if p:
            self.push_undo("Delete text")
            p.ocr, p.text_override = None, None
            self.show_text()
            self._update_thumb(self.cur)
            self.mark_dirty()

    def analyze_table(self):
        p = self.page()
        if not p:
            return
        for i in self._selected_area_indices():
            p.areas[i].kind = "table"
        self.recognize_area()

    def save_area_template(self):
        p = self.page()
        if not p or not p.areas:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Save Area Template", str(Path.home() / "areas.folioareas"),
                                            "Area templates (*.folioareas)")
        if fn:
            img = self.project.image(p)
            Path(fn).write_text(json.dumps({"size": [img.width, img.height], "areas": [a.to_list() for a in p.areas]}))

    def load_area_template(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Load Area Template", str(Path.home()), "Area templates (*.folioareas)")
        if not fn:
            return
        d = json.loads(Path(fn).read_text())
        sw, sh = d["size"]
        scope = self.selected_pages()
        if len(self.project.pages) > 1 and ask(self, "Apply the template to all pages?") == QMessageBox.Yes:
            scope = self.project.pages
        for p in scope:
            img = self.project.image(p)
            fx, fy = img.width / sw, img.height / sh
            p.areas = [D.Area(k, (int(r[0] * fx), int(r[1] * fy), int(r[2] * fx), int(r[3] * fy))) for k, r in d["areas"]]
        self._draw_areas()
        self.mark_dirty()

    # ================================================================== recognition
    def recognize_all(self):
        if not self.project.pages:
            self.open_images()
            return
        self.recognize_pages(self.project.pages)

    def recognize_page(self):
        if self.page():
            self.recognize_pages([self.page()])

    def recognize_pages(self, pages, analyze_only=False):
        if not ocr.available():
            error(self, "The OCR engine (tesseract) was not found.")
            return
        langs = self.lang.languages()
        proj = self.project
        pages = list(pages)

        def work(progress, cancel):
            for n, p in enumerate(pages):
                if cancel():
                    break
                progress(n, len(pages), f"{'Analyzing' if analyze_only else 'Recognizing'} page "
                                        f"{proj.pages.index(p) + 1 if p in proj.pages else n + 1} ({n + 1} of {len(pages)})...")
                if analyze_only:
                    keep = p.areas
                    p.areas = []
                    D.recognize_page(proj, p, langs)
                    p.text_override = None
                else:
                    if p.areas and all(a.kind in ("picture", "background") for a in p.areas):
                        p.areas = []
                    D.recognize_page(proj, p, langs)
            return len(pages)

        def done(_):
            self.mark_dirty()
            self.refresh_thumbs()
            self._draw_areas()
            self.show_text()
            n = sum(1 for p in pages for w in (p.ocr.words() if p.ocr else []) if w.conf < settings()["uncertain_threshold"])
            self.statusBar().showMessage(f"Recognition complete: {len(pages)} page(s), {n} uncertain word(s).")
        run_task(self, "Recognizing...", work, on_done=done)

    def analyze_page(self):
        if self.page():
            self.analyze_pages([self.page()])

    def analyze_all(self):
        self.analyze_pages(self.project.pages)

    def analyze_pages(self, pages):
        self.recognize_pages(pages, analyze_only=True)

    def recognize_area(self):
        p = self.page()
        sel = self._selected_area_indices()
        if not p or not sel:
            info(self, "Select an area first.")
            return
        rects = [p.areas[i].rect for i in sel]
        langs = self.lang.languages()

        def work(progress, cancel):
            D.recognize_page(self.project, p, langs, area_rects=rects)

        run_task(self, "Recognizing area...", work, on_done=lambda _: (self.show_text(), self._update_thumb(self.cur),
                                                                        self.mark_dirty()))

    def analyze_area(self):
        p = self.page()
        sel = self._selected_area_indices()
        if not p or not sel:
            return
        i = sel[0]
        a = p.areas[i]
        img = self.project.image(p)
        x, y, w, h = a.rect
        sub = img.crop((x, y, x + w, y + h))
        sub.info["dpi"] = img.info.get("dpi")
        langs = self.lang.languages()

        def work(progress, cancel):
            return ocr.recognize(sub, langs)

        def done(res):
            new = [D.Area("picture" if b.kind == "picture" else "text",
                          (b.bbox[0] + x, b.bbox[1] + y, b.bbox[2], b.bbox[3])) for b in res.blocks]
            p.areas[i:i + 1] = new or [a]
            self._draw_areas()
            self.mark_dirty()
        run_task(self, "Analyzing area...", work, on_done=done)

    # ================================================================== text pane
    def show_text(self):
        p = self.page()
        self._building_text = True
        self.text.clear()
        thr = settings()["uncertain_threshold"]
        user = set(settings()["user_words"])
        if not p:
            self._building_text = False
            return
        cur = self.text.textCursor()
        base = QTextCharFormat()
        if p.text_override is not None:
            cur.insertText(p.text_override)
            self.page_state.setText("  edited text  ")
        elif p.ocr:
            first = True
            for bi, b in enumerate(p.ocr.blocks):
                if b.kind == "picture":
                    continue
                if not first:
                    cur.insertBlock()
                    cur.insertBlock()
                first = False
                if b.kind == "table":
                    rows = b.rows or build_table(b.lines)
                    if rows:
                        ncol = max(len(r) for r in rows)
                        tf = QTextTableFormat()
                        tf.setCellPadding(3)
                        tf.setCellSpacing(0)
                        tf.setBorder(1)
                        t = cur.insertTable(len(rows), ncol, tf)
                        for ri, r in enumerate(rows):
                            for ci in range(ncol):
                                c = t.cellAt(ri, ci).firstCursorPosition()
                                c.insertText(r[ci] if ci < len(r) else "")
                        cur.movePosition(QTextCursor.End)
                    continue
                for li, l in enumerate(b.lines):
                    if li:
                        cur.insertBlock()
                    for wi, w in enumerate(l.words):
                        if wi:
                            cur.insertText(" ", base)
                        f = QTextCharFormat()
                        f.setProperty(WORD_PROP, f"{bi}:{li}:{wi}")
                        if w.conf < thr and w.text not in user:
                            f.setBackground(QColor(158, 236, 247))
                        cur.insertText(w.text, f)
            self.page_state.setText("  ✓ verified  " if p.verified else "")
        else:
            self.page_state.setText("  not recognized  ")
        self.text.moveCursor(QTextCursor.Start)
        self.text.document().clearUndoRedoStacks()
        self._building_text = False

    def _collect_text(self):
        doc = self.text.document()
        groups, cur = [], []
        it = doc.rootFrame().begin()
        while not it.atEnd():
            fr = it.currentFrame()
            blk = it.currentBlock()
            if fr is not None:
                from PySide6.QtGui import QTextTable
                if isinstance(fr, QTextTable):
                    if cur:
                        groups.append("\n".join(cur))
                        cur = []
                    rows = []
                    for r in range(fr.rows()):
                        cells = []
                        for c in range(fr.columns()):
                            cell = fr.cellAt(r, c)
                            cc = cell.firstCursorPosition()
                            cc.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
                            cells.append(cc.selectedText().replace(" ", " "))
                        rows.append("\t".join(cells))
                    groups.append("\n".join(rows))
            elif blk.isValid():
                t = blk.text()
                if t.strip():
                    cur.append(t)
                elif cur:
                    groups.append("\n".join(cur))
                    cur = []
            it += 1
        if cur:
            groups.append("\n".join(cur))
        return "\n\n".join(groups)

    def _text_changed(self):
        if self._building_text:
            return
        self._text_dirty = True
        self._text_timer.start()

    def _commit_text(self):
        if not getattr(self, "_text_dirty", False):
            return
        self._text_dirty = False
        p = self.page()
        if not p:
            return
        before = p.text()
        new = self._collect_text()
        if new != before:
            D.apply_text_edit(p, new)
            self.mark_dirty()

    def _text_cursor(self):
        if self._building_text:
            return
        f = self.text.textCursor().charFormat()
        v = f.property(WORD_PROP)
        p = self.page()
        if v and p and p.ocr:
            try:
                bi, li, wi = [int(x) for x in v.split(":")]
                w = p.ocr.blocks[bi].lines[li].words[wi]
            except Exception:
                return
            self.show_word(self.cur, w, scroll_text=False)

    def show_word(self, pi, w, scroll_text=True):
        r = QRectF(*w.bbox).adjusted(-3, -3, 3, 3)
        self.view.set_overlays("word", [(0, r)], pen=QPen(QColor(230, 60, 30), 2), brush=QBrush(QColor(255, 230, 0, 60)), z=16)
        self.view.ensure_visible(0, r)
        img = self.project.image(self.project.pages[pi])
        x, y, ww, hh = w.bbox
        crop = img.crop((max(0, x - 200), max(0, y - hh), min(img.width, x + ww + 200), min(img.height, y + 2 * hh)))
        pm = pil_to_pixmap(crop)
        self.zoom_lbl.setPixmap(pm.scaledToHeight(max(40, self.zoom_lbl.height() - 8), Qt.SmoothTransformation))
        if scroll_text and self.page() and self.page().ocr:
            for bi, b in enumerate(self.page().ocr.blocks):
                for li, l in enumerate(b.lines):
                    for wi, x_ in enumerate(l.words):
                        if x_ is w:
                            self._select_word_in_text(bi, li, wi)
                            return

    def _select_word_in_text(self, bi, li, wi):
        key = f"{bi}:{li}:{wi}"
        doc = self.text.document()
        blk = doc.begin()
        while blk.isValid():
            it = blk.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.charFormat().property(WORD_PROP) == key:
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                    self._building_text = True
                    self.text.setTextCursor(c)
                    self._building_text = False
                    self.text.ensureCursorVisible()
                    p = self.page()
                    self.show_word(self.cur, p.ocr.blocks[bi].lines[li].words[wi], scroll_text=False)
                    return
                it += 1
            blk = blk.next()

    def word_changed(self, pi):
        if pi == self.cur:
            self.show_text()
        self.mark_dirty()

    def find_uncertain(self, after, direction=1):
        thr = settings()["uncertain_threshold"]
        user = set(settings()["user_words"])
        seq = []
        for pi, p in enumerate(self.project.pages):
            if p.ocr and p.text_override is None:
                for w in p.ocr.words():
                    seq.append((pi, w))
        if direction < 0:
            seq.reverse()
        start = 0
        if after:
            for k, (pi, w) in enumerate(seq):
                if w is after[1]:
                    start = k + 1
                    break
        elif direction > 0 and self.cur > 0:
            start = next((k for k, (pi, w) in enumerate(seq) if pi >= self.cur), 0)
        for pi, w in seq[start:]:
            if w.conf < thr and w.text not in user:
                return pi, w
        return None

    def goto_error(self, d):
        self._commit_text()
        cur = getattr(self, "_err", None)
        nxt = self.find_uncertain(cur, d)
        if not nxt:
            self.statusBar().showMessage("No more uncertain words.")
            self._err = None
            return
        self._err = nxt
        if nxt[0] != self.cur:
            self.go_to_page(nxt[0])
        self.show_word(nxt[0], nxt[1])

    def verify(self):
        self._commit_text()
        if not any(p.ocr for p in self.project.pages):
            info(self, "Recognize the document first.")
            return
        VerifyDialog(self).exec()
        self.show_text()

    def mark_verified(self):
        for p in self.selected_pages():
            p.verified = True
            if p.ocr:
                for w in p.ocr.words():
                    w.conf = 100.0
        self.refresh_thumbs()
        self.show_text()
        self.mark_dirty()

    # ================================================================== image editing
    def push_undo(self, title):
        p = self.page()
        if not p:
            return
        self.undo_stack.append((title, p.id, self.project.image(p).copy(), copy.deepcopy(p.ocr),
                                copy.deepcopy(p.areas), p.text_override))
        self.undo_stack = self.undo_stack[-30:]
        self.redo_stack.clear()

    def _restore(self, snap, to_stack):
        title, pid, img, o, areas, txt = snap
        p = next((x for x in self.project.pages if x.id == pid), None)
        if not p:
            return
        to_stack.append((title, pid, self.project.image(p).copy(), copy.deepcopy(p.ocr), copy.deepcopy(p.areas),
                         p.text_override))
        img.info["dpi"] = img.info.get("dpi", (300, 300))
        self.project.set_image(p, img, keep_ocr=True)
        p.ocr, p.areas, p.text_override = o, areas, txt
        self.go_to_page(self.project.pages.index(p))
        self._update_thumb(self.cur)

    def undo(self):
        if self.text.hasFocus() and self.text.document().isUndoAvailable():
            self.text.undo()
            return
        if self.undo_stack:
            self._restore(self.undo_stack.pop(), self.redo_stack)

    def redo(self):
        if self.text.hasFocus() and self.text.document().isRedoAvailable():
            self.text.redo()
            return
        if self.redo_stack:
            self._restore(self.redo_stack.pop(), self.undo_stack)

    def _replace_image(self, p, img, keep_ocr=False):
        self.project.set_image(p, img, keep_ocr=keep_ocr)
        i = self.project.pages.index(p)
        self._update_thumb(i)
        if i == self.cur:
            self.go_to_page(i)
        self.mark_dirty()

    def apply_image_op(self, title, fn, scope=0):
        if not self.page():
            return
        if scope == 2:
            pages = list(self.project.pages)
        elif scope == 1:
            pages = self.selected_pages()
        else:
            pages = [self.page()]
        if len(pages) == 1:
            self.push_undo(title)
        proj = self.project

        def work(progress, cancel):
            out = []
            for n, p in enumerate(pages):
                if cancel():
                    break
                progress(n, len(pages), f"{title}: page {n + 1} of {len(pages)}")
                out.append((p, fn(proj.image(p))))
            return out

        def done(res):
            for p, img in res:
                proj.set_image(p, img)
            self.refresh_thumbs()
            self.go_to_page(self.cur)
            self.mark_dirty()
            self.statusBar().showMessage(f"{title}: done. Recognize the page(s) again to update the text.")
        run_task(self, f"{title}...", work, on_done=done)

    def apply_crop(self):
        p = self.page()
        if not p or not self._crop_rect:
            info(self, "Draw a crop rectangle on the image first.")
            return
        r = self._crop_rect
        rect = (int(r.x()), int(r.y()), int(r.width()), int(r.height()))
        self._crop_rect = None
        self.view.clear_overlays("crop")
        self.set_tool("select")
        self.apply_image_op("Crop", lambda im: imageproc.crop(im, rect), self.img_edit.scope.currentIndex())

    def split_pages(self, vertical=True):
        pages = self.selected_pages()
        if not pages:
            return
        for p in pages:
            i = self.project.pages.index(p)
            parts = imageproc.split_facing(self.project.image(p), vertical)
            self.project.set_image(p, parts[0])
            self.project.add_image(parts[1], p.name + " (2)", index=i + 1)
        self.refresh_thumbs()
        self.go_to_page(self.cur)
        self.mark_dirty()

    # ================================================================== edit helpers
    def copy(self):
        if self.text.hasFocus() or not self._selected_area_indices():
            self.text.copy()
        else:
            self.copy_area_image()

    def copy_area_image(self):
        p = self.page()
        sel = self._selected_area_indices()
        if not p:
            return
        img = self.project.image(p)
        if sel:
            x, y, w, h = p.areas[sel[0]].rect
            img = img.crop((x, y, x + w, y + h))
        QGuiApplication.clipboard().setImage(pil_to_qimage(img))
        self.statusBar().showMessage("Image copied to the Clipboard.")

    def delete_selection(self):
        if self.text.hasFocus():
            self.text.textCursor().removeSelectedText()
        else:
            self.delete_areas()

    def delete_pages(self):
        pages = self.selected_pages()
        if not pages:
            return
        if ask(self, f"Delete {len(pages)} page(s)?") != QMessageBox.Yes:
            return
        self.project.remove_pages(pages)
        self.refresh_thumbs(False)
        self.go_to_page(min(self.cur, len(self.project.pages) - 1))
        self.mark_dirty()

    def reorder_dialog(self):
        items = ["Reverse page order", "Restore page order after duplex scanning (odd pages first, then even in reverse)",
                 "Restore page order after duplex scanning (odd pages first, then even)"]
        it, ok = QInputDialog.getItem(self, "Reorder Pages", "Reorder:", items, 0, False)
        if not ok:
            return
        pg = self.project.pages
        if it == items[0]:
            self.project.pages = list(reversed(pg))
        else:
            half = (len(pg) + 1) // 2
            odd, even = pg[:half], pg[half:]
            if it == items[1]:
                even = list(reversed(even))
            out = []
            for i in range(half):
                out.append(odd[i])
                if i < len(even):
                    out.append(even[i])
            self.project.pages = out
        self.refresh_thumbs(False)
        self.go_to_page(0)
        self.mark_dirty()

    def find(self):
        t, ok = QInputDialog.getText(self, "Find", "Find what:", text=getattr(self, "_find", ""))
        if ok and t:
            self._find = t
            self.find_next()

    def find_next(self):
        t = getattr(self, "_find", "")
        if not t:
            return self.find()
        self._commit_text()
        if self.text.find(t):
            return
        for k in range(1, len(self.project.pages) + 1):
            i = (self.cur + k) % len(self.project.pages)
            if t.lower() in self.project.pages[i].text().lower():
                self.go_to_page(i)
                self.text.find(t)
                return
        self.statusBar().showMessage(f"'{t}' not found.")

    def replace(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Replace")
        f = QFormLayout(dlg)
        a, b = QLineEdit(getattr(self, "_find", "")), QLineEdit()
        f.addRow("Find what:", a)
        f.addRow("Replace with:", b)
        allp = QCheckBox("In all pages")
        allp.setChecked(True)
        f.addRow(allp)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Replace &All")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if not dlg.exec() or not a.text():
            return
        self._commit_text()
        n = 0
        pages = self.project.pages if allp.isChecked() else [self.page()]
        for p in pages:
            if p.text_override is not None:
                n += p.text_override.count(a.text())
                p.text_override = p.text_override.replace(a.text(), b.text())
            elif p.ocr:
                for w in p.ocr.words():
                    if a.text() in w.text:
                        n += w.text.count(a.text())
                        w.text = w.text.replace(a.text(), b.text())
                for blk in p.ocr.blocks:
                    if blk.rows:
                        blk.rows = [[c.replace(a.text(), b.text()) for c in r] for r in blk.rows]
        self.show_text()
        self.mark_dirty()
        self.statusBar().showMessage(f"{n} replacement(s) made.")

    def insert_symbol(self):
        syms = "© ® ™ § ¶ † ‡ • … – — « » „ “ ” ‘ ’ € £ ¥ ¢ ° ± × ÷ ≈ ≠ ≤ ≥ ∞ √ ∑ π µ α β γ δ ε ζ η θ λ ξ ω Ω ά έ ή ί ό ύ ώ ἀ ἁ ὁ ῥ ῶ ᾳ"
        s, ok = QInputDialog.getItem(self, "Symbol", "Insert symbol:", syms.split(), 0, True)
        if ok and s:
            self.text.insertPlainText(s)

    def page_properties(self):
        p = self.page()
        if not p:
            return
        img = self.project.image(p)
        dpi = img.info.get("dpi", (300, 300))
        n = sum(1 for _ in p.ocr.words()) if p.ocr else 0
        info(self, f"Page {self.cur + 1}: {p.name}\n\nImage size: {img.width} × {img.height} px\n"
                   f"Resolution: {int(dpi[0])} dpi\nPaper size: {img.width / dpi[0] * 25.4:.0f} × {img.height / dpi[1] * 25.4:.0f} mm\n"
                   f"Color: {'Black and white' if img.mode == '1' else 'Grayscale' if img.mode == 'L' else 'Color'}\n"
                   f"Areas: {len(p.areas)}\nRecognized words: {n}\nVerified: {'yes' if p.verified else 'no'}")

    def dictionaries(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("User Dictionary")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Words in the user dictionary are never marked as uncertain (one per line):"))
        ed = QPlainTextEdit("\n".join(settings()["user_words"]))
        v.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.resize(380, 420)
        if dlg.exec():
            settings()["user_words"] = sorted({w.strip() for w in ed.toPlainText().splitlines() if w.strip()})
            self.show_text()

    # ================================================================== saving / sending
    def export_pages(self):
        self._commit_text()
        return [exporters.ExportPage(self.project.image(p), p.ocr, p.text_override, p.name) for p in self.project.pages]

    def _ensure_recognized(self, then):
        todo = [p for p in self.project.pages if not p.recognized]
        if not self.project.pages:
            info(self, "There are no pages in the OCR project.")
            return
        if todo:
            r = ask(self, f"{len(todo)} page(s) have not been recognized. Recognize them now?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                langs = self.lang.languages()
                proj = self.project

                def work(progress, cancel):
                    for n, p in enumerate(todo):
                        if cancel():
                            raise RuntimeError("Cancelled")
                        progress(n, len(todo), f"Recognizing page {n + 1} of {len(todo)}...")
                        D.recognize_page(proj, p, langs)
                run_task(self, "Recognizing...", work, on_done=lambda _: (self.refresh_thumbs(), self.show_text(), then()))
                return
        then()

    def save_as(self, fmt):
        self.last_format = fmt
        self._ensure_recognized(lambda: self._save_as(fmt))

    def _save_as(self, fmt):
        base = Path(self.project.path).stem if self.project.path else (self.project.pages[0].name.split(" #")[0] if self.project.pages else "Document")
        fn = save_dialog(self, fmt, base)
        if not fn:
            return
        pages = self.export_pages()
        run_task(self, "Saving to file...", lambda progress, cancel: exporters.export(pages, fmt, fn),
                 on_done=lambda files: self._saved(files))

    def _saved(self, files, open_it=True):
        self.statusBar().showMessage("Saved: " + ", ".join(files))
        if open_it and files:
            paths.open_with_default(files[0])

    def save_page_images(self):
        if not self.project.pages:
            return
        fn, flt = QFileDialog.getSaveFileName(self, "Save Page Images", str(Path.home() / "Page.png"),
                                              "PNG (*.png);;JPEG (*.jpg);;Multi-page TIFF (*.tif);;BMP (*.bmp)")
        if not fn:
            return
        ext = Path(fn).suffix.lower().lstrip(".") or "png"
        ext = {"jpeg": "jpg", "tiff": "tif"}.get(ext, ext)
        pages = [exporters.ExportPage(self.project.image(p)) for p in self.selected_pages()] if len(self.thumbs.selected_indices()) > 1 \
            else [exporters.ExportPage(self.project.image(p)) for p in self.project.pages]
        files = exporters.export_images(pages, fn, ext)
        self.statusBar().showMessage(f"Saved {len(files)} image file(s).")

    def send_to(self, fmt):
        def go():
            out = Path(tempfile.mkdtemp(prefix="folio-send-")) / ((Path(self.project.path).stem if self.project.path else "Document")
                                                               + exporters.FORMATS[fmt][1])
            pages = self.export_pages()

            def done(files):
                f = files[0]
                if fmt in ("odt", "docx", "xlsx", "pptx") and paths.which_external("libreoffice"):
                    paths.run_external(["libreoffice", f])
                else:
                    paths.open_with_default(f)
            run_task(self, "Sending...", lambda progress, cancel: exporters.export(pages, fmt, str(out)), on_done=done)
        self._ensure_recognized(go)

    def send_to_pdf_editor(self):
        def go():
            out = Path(tempfile.mkdtemp(prefix="folio-send-")) / ((Path(self.project.path).stem if self.project.path else "Document") + ".pdf")
            pages = self.export_pages()
            run_task(self, "Creating PDF...", lambda progress, cancel: exporters.export(pages, "pdf", str(out)),
                     on_done=lambda files: paths.launch_sibling("pdf", files[0]))
        self._ensure_recognized(go)

    def send_clipboard(self):
        self._ensure_recognized(lambda: QGuiApplication.clipboard().setText(exporters.plain_text(self.export_pages(), "\n\n")))

    def email(self, what):
        if not self.project.pages:
            return
        tmp = Path(tempfile.mkdtemp(prefix="folio-mail-"))
        if what == "images":
            files = exporters.export_images([exporters.ExportPage(self.project.image(p)) for p in self.project.pages],
                                            tmp / "Page.png", "png")
            args = ["xdg-email", "--subject", "Images"]
            for f in files:
                args += ["--attach", f]
            paths.run_external(args)
        else:
            def go():
                f = str(tmp / "Document.docx")
                exporters.export(self.export_pages(), "docx", f)
                paths.run_external(["xdg-email", "--subject", "Document", "--attach", f])
            self._ensure_recognized(go)

    def print_text(self):
        self._commit_text()
        pr = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(pr, self).exec():
            doc = QTextDocument()
            doc.setDefaultFont(QFont("DejaVu Sans", 11))
            doc.setPlainText(exporters.plain_text(self.export_pages(), "\n\n"))
            doc.print_(pr)

    def print_images(self):
        if not self.project.pages:
            return
        pr = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(pr, self).exec():
            p = QPainter(pr)
            for i, pg in enumerate(self.project.pages):
                if i:
                    pr.newPage()
                q = pil_to_qimage(self.project.image(pg))
                r = p.viewport()
                s = q.size().scaled(r.size(), Qt.KeepAspectRatio)
                p.setViewport(r.x(), r.y(), s.width(), s.height())
                p.setWindow(q.rect())
                p.drawImage(0, 0, q)
                p.setViewport(r)
            p.end()

    # ================================================================== projects
    def _confirm_discard(self):
        self._commit_text()
        if not self.project.dirty or not self.project.pages:
            return True
        r = ask(self, "Save changes to the current OCR project?", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Yes:
            return self.save_project()
        return True

    def new_project(self):
        if not self._confirm_discard():
            return
        self.project = D.Project()
        self.undo_stack.clear()
        self.refresh_thumbs(False)
        self.go_to_page(-1)
        self._update_title()

    def close_document(self):
        self.new_project()

    def open_project_dialog(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open OCR Project", str(Path.home()), f"Folio OCR projects (*{D.PROJECT_EXT})")
        if fn:
            self.open_project(fn)

    def open_project(self, fn):
        if not self._confirm_discard():
            return
        try:
            self.project = D.Project.load(fn)
        except Exception as e:
            error(self, f"Cannot open the OCR project:\n{e}")
            return
        if self.project.languages:
            self.lang.set_languages(self.project.languages)
        settings().add_recent("recent_documents", fn)
        settings()["last_project"] = fn
        self.refresh_thumbs(False)
        self.go_to_page(0)
        self._update_title()

    def save_project(self, save_as=False):
        self._commit_text()
        fn = self.project.path
        if save_as or not fn:
            fn, _ = QFileDialog.getSaveFileName(self, "Save OCR Project", str(Path.home() / f"Untitled{D.PROJECT_EXT}"),
                                                f"Folio OCR projects (*{D.PROJECT_EXT})")
            if not fn:
                return False
            if not fn.endswith(D.PROJECT_EXT):
                fn += D.PROJECT_EXT
        self.project.languages = self.lang.languages()
        try:
            self.project.save(fn)
        except Exception as e:
            error(self, f"Cannot save the OCR project:\n{e}")
            return False
        settings().add_recent("recent_documents", fn)
        settings()["last_project"] = fn
        self._update_title()
        self.statusBar().showMessage(f"Saved {fn}")
        return True

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        rec = [r for r in settings()["recent_documents"] if Path(r).exists()][:12]
        for i, r in enumerate(rec):
            m.addAction(f"&{i + 1}. {Path(r).name}", lambda r=r: self.open_project(r) if r.endswith(D.PROJECT_EXT)
                        else self.add_files([r]))
        if not rec:
            m.addAction("(none)").setEnabled(False)

    def options(self):
        if OptionsDialog(self).exec():
            self.lang.set_languages(settings()["languages"])
            self.show_text()

    def help(self):
        info(self, "Workflow: Open Image / Scan → Recognize → Verify → Save As / Send To.\n\n"
                   "• Draw areas with the area tools above the image; numbers show reading order.\n"
                   "• Right-click an area to change its type or recognize it.\n"
                   "• Uncertain words are highlighted in the text pane (Alt+Down / Alt+Up to jump).\n"
                   "• Edit Page Images (Ctrl+I) for deskew, crop, levels, eraser and more.\n"
                   "• Projects (*.folio) keep images, areas and text together.\n\n"
                   "More OCR languages: copy *.traineddata files into ~/.local/share/folio/tessdata")

    def closeEvent(self, e):
        if self._confirm_discard():
            e.accept()
        else:
            e.ignore()


class HotFolderDialog(QDialog):
    """Hot Folder: watch a folder and convert new files automatically while the dialog is open."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hot Folder")
        self.resize(560, 420)
        hs = settings()["hot_folder"]
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.inp = QLineEdit(hs.get("input", ""))
        self.out = QLineEdit(hs.get("output", ""))
        for le in (self.inp, self.out):
            h = QHBoxLayout()
            b = QPushButton("...")
            b.clicked.connect(lambda _=False, le=le: le.setText(QFileDialog.getExistingDirectory(self, "Folder", le.text() or str(Path.home())) or le.text()))
            h.addWidget(le)
            h.addWidget(b)
            f.addRow("Watch folder:" if le is self.inp else "Output folder:", h)
        self.fmt = QComboBox()
        for k, (label, ext, _) in exporters.FORMATS.items():
            self.fmt.addItem(label, k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(hs.get("format", "pdf"))))
        f.addRow("Save as:", self.fmt)
        self.lang = LanguageCombo(store=False)
        f.addRow("Languages:", self.lang)
        self.move = QCheckBox("Move processed originals to a 'processed' subfolder")
        self.move.setChecked(True)
        f.addRow(self.move)
        v.addLayout(f)
        self.log = QPlainTextEdit(readOnly=True)
        v.addWidget(self.log, 1)
        h = QHBoxLayout()
        self.btn = QPushButton("&Start")
        self.btn.clicked.connect(self.toggle)
        h.addStretch()
        h.addWidget(self.btn)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        h.addWidget(close)
        v.addLayout(h)
        self.timer = QTimer(self, interval=5000, timeout=self.poll)
        self.busy = False
        self.seen = set()

    def toggle(self):
        if self.timer.isActive():
            self.timer.stop()
            self.btn.setText("&Start")
            self.log.appendPlainText("Stopped.")
            return
        if not Path(self.inp.text()).is_dir() or not self.out.text():
            error(self, "Choose an existing watch folder and an output folder.")
            return
        Path(self.out.text()).mkdir(parents=True, exist_ok=True)
        s = settings()
        s.data["hot_folder"].update({"input": self.inp.text(), "output": self.out.text(), "format": self.fmt.currentData()})
        s.save()
        self.timer.start()
        self.btn.setText("&Stop")
        self.log.appendPlainText(f"Watching {self.inp.text()} ...")
        self.poll()

    def poll(self):
        if self.busy:
            return
        files = [f for f in sorted(Path(self.inp.text()).iterdir()) if f.is_file() and D.kind_of(f) in ("image", "pdf", "office") and str(f) not in self.seen]
        if not files:
            return
        f = files[0]
        self.seen.add(str(f))
        fmt = self.fmt.currentData()
        out = Path(self.out.text()) / (f.stem + exporters.FORMATS[fmt][1])
        self.busy = True
        self.log.appendPlainText(f"Processing {f.name} ...")
        from ..core import pipeline
        langs = self.lang.languages()

        def done(res):
            self.busy = False
            self.log.appendPlainText(f"  → {out.name}")
            self._archive(f)

        def fail(msg):
            self.busy = False
            self.log.appendPlainText(f"  ! {msg}")
            self._archive(f, "failed")
        run_task(self, "", lambda progress, cancel: pipeline.convert([str(f)], fmt, str(out), langs),
                 on_done=done, on_error=fail, modal=False)

    def _archive(self, f, sub="processed"):
        d = f.parent / sub
        d.mkdir(exist_ok=True)
        if self.move.isChecked() or sub == "failed":
            try:
                f.rename(d / f.name)
            except OSError:
                pass

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)


def run(argv):
    app = make_app("ocr", argv)
    w = OcrEditor()
    w.show()
    files = [a for a in argv if not a.startswith("-") and Path(a).exists()]
    if files:
        projects = [f for f in files if f.endswith(D.PROJECT_EXT)]
        if projects:
            w.open_project(projects[0])
        else:
            QTimer.singleShot(100, lambda: w.add_files(files))
    elif settings()["ocr_startup"] == "last" and settings()["last_project"] and Path(settings()["last_project"]).exists():
        w.open_project(settings()["last_project"])
    return app.exec()
