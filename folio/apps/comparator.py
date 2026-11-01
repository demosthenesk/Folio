"""Folio Compare — two document panes + a list of differences."""
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QGuiApplication, QKeySequence, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QPushButton, QSplitter, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APPS
from ..core import compare as cmp
from ..core import ocr, paths
from ..core.document import OPEN_FILTER
from ..core.settings import settings
from ..ui.qtutil import app_icon, error, icon, info, make_app, pil_to_qimage, run_task
from ..ui.widgets import AboutDialog, LanguageCombo, MultiPageView

COLORS = {"deleted": QColor(240, 80, 70, 70), "added": QColor(60, 190, 90, 70), "changed": QColor(255, 196, 0, 80)}


class DocPane(QWidget):
    def __init__(self, title, owner, side):
        super().__init__()
        self.owner, self.side = owner, side
        self.path = None
        self.doc = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#2d3a4a} QLabel{color:white;font-weight:bold}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 5, 6, 5)
        self.title = QLabel(title)
        h.addWidget(self.title)
        self.name = QLabel("")
        self.name.setStyleSheet("color:#cfe1fb;font-weight:normal")
        h.addWidget(self.name, 1)
        self.open_btn = QToolButton(text="Open...")
        self.open_btn.setIcon(icon("document-open"))
        self.open_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.open_btn.setStyleSheet("QToolButton{color:white;background:transparent;border:1px solid #5b6b7d;border-radius:4px;padding:2px 6px}")
        self.open_btn.clicked.connect(self.choose)
        h.addWidget(self.open_btn)
        v.addWidget(bar)
        self.stack = QStackedWidget()
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch()
        big = QPushButton(icon("document-open"), f"  Open {title.title()}...")
        big.setIconSize(QSize(32, 32))
        big.setMinimumHeight(56)
        big.clicked.connect(self.choose)
        ev.addWidget(big, 0, Qt.AlignCenter)
        hint = QLabel("or drop a file here\nPDF, images, DOCX, ODT, RTF, TXT, XLSX, PPTX...")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#777")
        ev.addWidget(hint)
        ev.addStretch()
        self.view = MultiPageView()
        self.view.contextRequested.connect(self._context)
        self.stack.addWidget(empty)
        self.stack.addWidget(self.view)
        v.addWidget(self.stack, 1)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.owner.load(self.side, urls[0])

    def choose(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open Document", str(Path.home()), OPEN_FILTER)
        if fn:
            self.owner.load(self.side, fn)

    def show_doc(self, path, doc: cmp.DocText):
        self.path, self.doc = path, doc
        self.name.setText(Path(path).name)
        imgs = doc.images
        self.view.set_document([(im.width, im.height) for im in imgs],
                               lambda i, s: pil_to_qimage(imgs[i].resize((max(1, int(imgs[i].width * s)),
                                                                           max(1, int(imgs[i].height * s))))))
        self.stack.setCurrentIndex(1)
        self.view.fit_width()

    def reset(self):
        self.path = self.doc = None
        self.name.setText("")
        self.view.clear()
        self.stack.setCurrentIndex(0)

    def _context(self, page, pt, gpos):
        m = QMenu(self)
        m.addAction("&Copy Text", self.owner.copy_difference)
        m.addSeparator()
        m.addAction("I&gnore Difference", self.owner.ignore_difference)
        m.addSeparator()
        m.addAction("Zoo&m In", self.view.zoom_in)
        m.addAction("&Zoom Out", self.view.zoom_out)
        m.addAction("&Fit to Width", self.view.fit_width)
        m.addSeparator()
        for z in (0.5, 1.0, 1.5, 2.0):
            m.addAction(f"{int(z * 100)}%", lambda z=z: self.view.set_zoom(z))
        m.exec(gpos)


class Comparator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APPS["compare"][0])
        self.setWindowIcon(app_icon("compare"))
        self.resize(1400, 860)
        self.diffs = []
        self.docs = {1: None, 2: None}
        self.paths = {1: None, 2: None}
        self.pane1 = DocPane("DOCUMENT 1", self, 1)
        self.pane2 = DocPane("DOCUMENT 2", self, 2)
        self.side = self._build_side()
        sp = QSplitter()
        sp.addWidget(self.pane1)
        sp.addWidget(self.pane2)
        sp.addWidget(self.side)
        sp.setSizes([560, 560, 300])
        self.setCentralWidget(sp)
        self._build_menu()
        self.sync = True
        self._syncing = False
        self.pane1.view.verticalScrollBar().valueChanged.connect(lambda v: self._sync_scroll(1))
        self.pane2.view.verticalScrollBar().valueChanged.connect(lambda v: self._sync_scroll(2))
        self.statusBar().showMessage("Open two documents to compare.")

    # -- UI
    def _build_side(self):
        w = QWidget()
        w.setMinimumWidth(260)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#2d3a4a} QLabel{color:white;font-weight:bold}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 7, 10, 7)
        self.side_title = QLabel("COMPARE")
        h.addWidget(self.side_title)
        v.addWidget(bar)
        self.side_stack = QStackedWidget()
        # options page
        opt = QWidget()
        ov = QVBoxLayout(opt)
        ov.addWidget(QLabel("Document language:"))
        cs = settings()["compare"]
        self.lang = LanguageCombo(settings()["languages"], store=False)
        ov.addWidget(self.lang)
        ov.addSpacing(10)
        self.cb_punct = QCheckBox("&Find differences in punctuation")
        self.cb_punct.setChecked(cs.get("punctuation", False))
        self.cb_one = QCheckBox("Find &one-letter differences")
        self.cb_one.setChecked(cs.get("one_letter", False))
        self.cb_head = QCheckBox("Find differences in &headers and footers")
        self.cb_head.setChecked(cs.get("headers", True))
        for c in (self.cb_punct, self.cb_one, self.cb_head):
            ov.addWidget(c)
        ov.addSpacing(12)
        self.cmp_btn = QPushButton(icon("compare"), "  &Compare")
        self.cmp_btn.setMinimumHeight(46)
        self.cmp_btn.setStyleSheet("QPushButton{background:#3d8ae8;color:white;font-weight:bold;border-radius:6px}"
                                   "QPushButton:disabled{background:#9db9de}")
        self.cmp_btn.clicked.connect(self.compare)
        ov.addWidget(self.cmp_btn)
        ov.addStretch()
        self.side_stack.addWidget(opt)
        # results page
        res = QWidget()
        rv = QVBoxLayout(res)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        rv.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAlternatingRowColors(True)
        self.list.currentRowChanged.connect(self.select_difference)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        rv.addWidget(self.list, 1)
        h = QHBoxLayout()
        b_prev = QToolButton(icon=icon("go-up"), toolTip="Previous Difference")
        b_next = QToolButton(icon=icon("go-down"), toolTip="Next Difference")
        b_ign = QToolButton(text="Ignore", toolTip="Ignore Difference")
        b_copy = QToolButton(text="Copy", toolTip="Copy Difference")
        b_prev.clicked.connect(self.prev_difference)
        b_next.clicked.connect(self.next_difference)
        b_ign.clicked.connect(self.ignore_difference)
        b_copy.clicked.connect(self.copy_difference)
        for b in (b_prev, b_next, b_ign, b_copy):
            h.addWidget(b)
        h.addStretch()
        self.save_btn = QToolButton(text="Save...")
        self.save_btn.setIcon(icon("document-save"))
        self.save_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.save_btn.setPopupMode(QToolButton.InstantPopup)
        self.save_menu = QMenu(self)
        self.save_btn.setMenu(self.save_menu)
        h.addWidget(self.save_btn)
        rv.addLayout(h)
        again = QPushButton("&New Comparison")
        again.clicked.connect(self.new_comparison)
        rv.addWidget(again)
        self.side_stack.addWidget(res)
        v.addWidget(self.side_stack, 1)
        return w

    def _build_menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("N&ew Comparison", self.new_comparison, QKeySequence.New)
        f.addSeparator()
        f.addAction(icon("document-open"), "&Open Document...", self.open_documents, QKeySequence.Open)
        self.recent_menu = f.addMenu("&Recent Comparisons")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        f.addAction("&Close Document", self.close_document)
        self.save_root = f.addMenu("&Save")
        f.addSeparator()
        f.addAction("E&xit", self.close, QKeySequence.Quit)
        self._fill_save(self.save_root)
        self._fill_save(self.save_menu)
        e = mb.addMenu("&Edit")
        e.addAction("&Copy Difference", self.copy_difference, QKeySequence.Copy)
        e.addAction("I&gnore Difference", self.ignore_difference, QKeySequence.Delete)
        e.addAction("&Select All Differences", lambda: self.list.selectAll())
        e.addAction("&Restore Ignored Differences", self.restore_ignored)
        v = mb.addMenu("Vie&w")
        v.addAction(icon("zoom-in"), "&Zoom In", lambda: [p.view.zoom_in() for p in (self.pane1, self.pane2)], QKeySequence.ZoomIn)
        v.addAction(icon("zoom-out"), "Zoom &Out", lambda: [p.view.zoom_out() for p in (self.pane1, self.pane2)], QKeySequence.ZoomOut)
        v.addAction(icon("fit-width"), "Fit to &Width", lambda: [p.view.fit_width() for p in (self.pane1, self.pane2)])
        v.addSeparator()
        v.addAction("N&ext Page", lambda: [p.view.go_to_page(p.view.cur_page + 1) for p in (self.pane1, self.pane2)], "PgDown")
        v.addAction("&Previous Page", lambda: [p.view.go_to_page(p.view.cur_page - 1) for p in (self.pane1, self.pane2)], "PgUp")
        v.addSeparator()
        a = v.addAction("S&ynchronize Scrolling")
        a.setCheckable(True)
        a.setChecked(True)
        a.toggled.connect(lambda x: setattr(self, "sync", x))
        c = mb.addMenu("&Compare")
        c.addAction(icon("compare"), "&Compare Documents", self.compare, "Ctrl+R")
        c.addSeparator()
        c.addAction("N&ext Difference", self.next_difference, "F8")
        c.addAction("&Previous Difference", self.prev_difference, "Shift+F8")
        c.addSeparator()
        c.addAction("I&gnore Difference", self.ignore_difference)
        c.addSeparator()
        adv = c.addMenu("&Advanced")
        pm = adv.addMenu("&PDF Recognition Mode")
        grp = QActionGroup(self)
        cur = settings()["pdf_mode"]
        self.pdf_mode = cur
        for k, t in (("ocr", "Use OC&R"), ("auto", "A&utomatically Choose between OCR and Text from PDF"),
                     ("text", "Use Only &Text from PDF")):
            a = pm.addAction(t)
            a.setCheckable(True)
            a.setChecked(k == cur)
            a.setActionGroup(grp)
            a.triggered.connect(lambda _=False, k=k: setattr(self, "pdf_mode", k))
        h = mb.addMenu("&Help")
        h.addAction("&About", lambda: AboutDialog("compare", self).exec())

    def _fill_save(self, m):
        m.clear()
        m.addAction("Document &1: PDF with Comments...", lambda: self.save_result("pdf", 1))
        m.addAction("Document 1: D&OCX in Track Changes Mode...", lambda: self.save_result("docx", 1))
        m.addSeparator()
        m.addAction("Document &2: PDF with Comments...", lambda: self.save_result("pdf", 2))
        m.addAction("Document 2: DO&CX in Track Changes Mode...", lambda: self.save_result("docx", 2))
        m.addSeparator()
        m.addAction("Co&mparison Report...", lambda: self.save_result("report", 0))

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        rec = settings()["recent_comparisons"]
        if not rec:
            m.addAction("(none)").setEnabled(False)
        for r in rec[:10]:
            a, b = r.split("|", 1)
            m.addAction(f"{Path(a).name}  ↔  {Path(b).name}", lambda a=a, b=b: (self.load(1, a), self.load(2, b)))
        m.addSeparator()
        m.addAction("&Clear This List", lambda: settings().__setitem__("recent_comparisons", []))

    # -- documents
    def open_documents(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Open Document(s)", str(Path.home()), OPEN_FILTER)
        if not fns:
            return
        if len(fns) >= 2:
            self.load(1, fns[0])
            self.load(2, fns[1])
        else:
            self.load(1 if not self.paths[1] else 2, fns[0])

    def load(self, side, path):
        if not Path(path).exists():
            error(self, f"File {path} not found.")
            return
        self.paths[side] = path
        self.docs[side] = None
        self._clear_results()
        pane = self.pane1 if side == 1 else self.pane2
        mode = self.pdf_mode
        langs = self.lang.languages()

        def work(progress, cancel):
            # quick view first (text layer only); OCR happens when comparing if needed
            return cmp.extract(path, langs, "text", progress=progress, cancel=cancel)

        def done(doc):
            pane.show_doc(path, doc)
            self.statusBar().showMessage(f"Loaded {Path(path).name}: {len(doc.images)} page(s).")
            self.cmp_btn.setEnabled(True)
        run_task(self, "Loading the document. Please wait...", work, on_done=done)

    def close_document(self):
        for side, pane in ((2, self.pane2), (1, self.pane1)):
            if self.paths[side]:
                self.paths[side] = None
                self.docs[side] = None
                pane.reset()
                break
        self._clear_results()

    def new_comparison(self):
        for side, pane in ((1, self.pane1), (2, self.pane2)):
            self.paths[side] = None
            self.docs[side] = None
            pane.reset()
        self._clear_results()

    # -- comparison
    def compare(self):
        if not (self.paths[1] and self.paths[2]):
            info(self, "Open two documents first (DOCUMENT 1 and DOCUMENT 2).")
            return
        cs = settings()
        cs.data["compare"].update({"punctuation": self.cb_punct.isChecked(), "one_letter": self.cb_one.isChecked(),
                                   "headers": self.cb_head.isChecked()})
        cs.save()
        langs = self.lang.languages()
        mode = self.pdf_mode
        p1, p2 = self.paths[1], self.paths[2]
        opts = dict(punctuation=self.cb_punct.isChecked(), one_letter=self.cb_one.isChecked(),
                    headers=self.cb_head.isChecked())

        def work(progress, cancel):
            d1 = cmp.extract(p1, langs, mode, progress=progress, cancel=cancel)
            d2 = cmp.extract(p2, langs, mode, progress=progress, cancel=cancel)
            progress(1, 1, "Comparing...")
            return d1, d2, cmp.compare(d1, d2, **opts)

        def done(res):
            d1, d2, diffs = res
            self.docs = {1: d1, 2: d2}
            self.diffs = diffs
            self.options = opts
            settings().add_recent("recent_comparisons", f"{p1}|{p2}")
            self.pane1.show_doc(p1, d1)
            self.pane2.show_doc(p2, d2)
            self._show_results()
        run_task(self, "Comparing documents...", work, on_done=done)

    def _clear_results(self):
        self.diffs = []
        for p in (self.pane1, self.pane2):
            for k in ("diff-deleted", "diff-added", "diff-changed", "sel"):
                p.view.clear_overlays(k)
        self.side_stack.setCurrentIndex(0)
        self.side_title.setText("COMPARE")

    def _show_results(self):
        self.side_stack.setCurrentIndex(1)
        self.list.clear()
        for d in self.diffs:
            it = QListWidgetItem(self._diff_text(d))
            it.setData(Qt.UserRole, d.id)
            it.setForeground(QColor("#999") if d.ignored else QColor("#111"))
            self.list.addItem(it)
        s = cmp.summary(self.diffs)
        self.side_title.setText(f"DIFFERENCES ({s['total']})")
        if not self.diffs:
            self.summary.setText("<b>No differences found.</b> The documents have the same text.")
        else:
            self.summary.setText(f"Body text: {s['body']} · Headers and footers: {s['header']} · Numbering: {s['numbering']}"
                                 + ("<br><small>Text was recognized with OCR.</small>"
                                    if (self.docs[1].used_ocr or self.docs[2].used_ocr) else ""))
        self._draw_overlays()
        if self.diffs:
            self.list.setCurrentRow(0)

    def _diff_text(self, d):
        cat = {"header": "Headers and footers", "numbering": "Numbering", "body": ""}[d.category]
        pg = f"[page {d.page1 + 1} / {d.page2 + 1}]"
        t = d.label()
        if len(t) > 160:
            t = t[:157] + "..."
        return f"{t}\n{pg} {cat}" + ("  (ignored)" if d.ignored else "")

    def _draw_overlays(self):
        for side, pane in ((1, self.pane1), (2, self.pane2)):
            rects = {}
            for d in self.diffs:
                if d.ignored:
                    continue
                present = d.kind != ("added" if side == 1 else "deleted")
                if not present:
                    continue
                for pg, r in (d.rects1 if side == 1 else d.rects2):
                    rects.setdefault(d.kind, []).append((pg, QRectF(*r).adjusted(-2, -2, 2, 2)))
            for k in COLORS:
                pane.view.clear_overlays("diff-" + k)
            for kind, rs in rects.items():
                pane.view.set_overlays("diff-" + kind, rs, brush=QBrush(COLORS[kind]))

    def current(self):
        it = self.list.currentItem()
        if not it:
            return None
        return self.diffs[it.data(Qt.UserRole)]

    def select_difference(self, row):
        d = self.current()
        if not d:
            return
        for side, pane in ((1, self.pane1), (2, self.pane2)):
            rs = d.rects1 if side == 1 else d.rects2
            pane.view.set_overlays("sel", [(p, QRectF(*r).adjusted(-4, -4, 4, 4)) for p, r in rs],
                                   pen=QPen(QColor(30, 90, 220), 2), brush=QBrush(Qt.NoBrush), z=8)
            if rs:
                self._syncing = True
                pane.view.ensure_visible(rs[0][0], QRectF(*rs[0][1]))
                self._syncing = False
        self.statusBar().showMessage(d.label())

    def next_difference(self):
        if self.list.count():
            self.list.setCurrentRow(min(self.list.count() - 1, self.list.currentRow() + 1))

    def prev_difference(self):
        if self.list.count():
            self.list.setCurrentRow(max(0, self.list.currentRow() - 1))

    def ignore_difference(self):
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()} or {self.list.currentRow()})
        for r in rows:
            if r < 0:
                continue
            d = self.diffs[self.list.item(r).data(Qt.UserRole)]
            d.ignored = True
            self.list.item(r).setText(self._diff_text(d))
            self.list.item(r).setForeground(QColor("#999"))
        self._draw_overlays()
        self.side_title.setText(f"DIFFERENCES ({cmp.summary(self.diffs)['total']})")

    def restore_ignored(self):
        for d in self.diffs:
            d.ignored = False
        self._show_results()

    def copy_difference(self):
        items = self.list.selectedItems() or ([self.list.currentItem()] if self.list.currentItem() else [])
        txt = "\n".join(self.diffs[i.data(Qt.UserRole)].label() for i in items)
        if txt:
            QGuiApplication.clipboard().setText(txt)

    def _list_menu(self, pos):
        m = QMenu(self)
        m.addAction("&Copy Difference", self.copy_difference)
        m.addAction("Ign&ore Difference", self.ignore_difference)
        m.exec(self.list.mapToGlobal(pos))

    def _sync_scroll(self, src):
        if not self.sync or self._syncing:
            return
        a = (self.pane1 if src == 1 else self.pane2).view.verticalScrollBar()
        b = (self.pane2 if src == 1 else self.pane1).view.verticalScrollBar()
        if a.maximum() <= 0:
            return
        self._syncing = True
        b.setValue(int(a.value() / a.maximum() * b.maximum()))
        self._syncing = False

    # -- saving
    def save_result(self, kind, side):
        if not self.diffs and kind != "docx":
            if not self.docs[1]:
                info(self, "Compare the documents first.")
                return
        d = self.docs.get(side) if side else None
        base = Path(self.paths[side] if side else self.paths[1] or "comparison").stem
        if kind == "pdf":
            fn, _ = QFileDialog.getSaveFileName(self, "Save", str(Path.home() / f"{base} (comments).pdf"), "PDF (*.pdf)")
        elif kind == "docx":
            fn, _ = QFileDialog.getSaveFileName(self, "Save", str(Path.home() / f"{base} (changes).docx"), "Word Document (*.docx)")
        else:
            fn, _ = QFileDialog.getSaveFileName(self, "Save", str(Path.home() / "Comparison report.docx"),
                                                "Word Document (*.docx);;HTML (*.html);;Text (*.txt)")
        if not fn:
            return
        try:
            if kind == "pdf":
                cmp.save_pdf_with_comments(self.diffs, d, side, fn)
            elif kind == "docx":
                other = self.docs[2 if side == 1 else 1]
                cmp.save_docx_track_changes(other, d, fn)  # tracked changes from the other version to this one
            else:
                cmp.save_report(self.diffs, self.docs[1], self.docs[2], fn, self.options)
            self.statusBar().showMessage(f"Saved {fn}")
            paths.open_with_default(fn)
        except Exception as e:
            error(self, f"Cannot save file. Please choose another location.\n{e}")


def run(argv):
    app = make_app("compare", argv)
    w = Comparator()
    w.show()
    files = [a for a in argv if not a.startswith("-") and Path(a).exists()]
    if files:
        w.load(1, files[0])
    if len(files) > 1:
        w.load(2, files[1])
    return app.exec()
