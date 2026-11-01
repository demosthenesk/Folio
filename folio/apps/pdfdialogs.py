"""Dialogs of the PDF editor (security, reduce size, watermark, header/footer, recognize, properties...)."""
import datetime
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QRadioButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..ui.widgets import LanguageCombo


def _bb(dlg, ok_text=None):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    if ok_text:
        bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class PageScope(QGroupBox):
    def __init__(self, count, current, title="Pages"):
        super().__init__(title)
        self.count, self.current = count, current
        v = QVBoxLayout(self)
        self.all = QRadioButton("&All")
        self.cur = QRadioButton(f"&Current page ({current + 1})")
        self.rng = QRadioButton("Page rang&e:")
        self.spec = QLineEdit(placeholderText="Example: 1,3,5-7")
        self.all.setChecked(True)
        v.addWidget(self.all)
        v.addWidget(self.cur)
        h = QHBoxLayout()
        h.addWidget(self.rng)
        h.addWidget(self.spec)
        v.addLayout(h)
        self.spec.textEdited.connect(lambda _: self.rng.setChecked(True))

    def pages(self):
        from ..core.document import parse_page_range
        if self.all.isChecked():
            return list(range(self.count))
        if self.cur.isChecked():
            return [self.current]
        return parse_page_range(self.spec.text(), self.count)


class RecognizeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recognize Document")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Recognized text will be placed on an invisible text layer under the page images."))
        h = QHBoxLayout()
        h.addWidget(QLabel("OCR language&s:"))
        self.lang = LanguageCombo()
        h.addWidget(self.lang, 1)
        v.addLayout(h)
        g = QGroupBox("Pages")
        gl = QVBoxLayout(g)
        self.only_missing = QRadioButton("Only pages without a text layer")
        self.all = QRadioButton("All pages (replace nothing, add a new text layer)")
        self.only_missing.setChecked(True)
        gl.addWidget(self.only_missing)
        gl.addWidget(self.all)
        v.addWidget(g)
        g = QGroupBox("Preprocessing")
        gl = QVBoxLayout(g)
        gl.addWidget(QLabel("You can improve recognition quality by enabling image preprocessing.\n"
                            "This may change the appearance of your document."))
        self.orient = QCheckBox("Correc&t page orientation")
        gl.addWidget(self.orient)
        v.addWidget(g)
        v.addWidget(_bb(self, "&Recognize"))


class SecurityDialog(QDialog):
    def __init__(self, parent=None, current=None):
        super().__init__(parent)
        self.setWindowTitle("Password Security")
        cur = current or {}
        v = QVBoxLayout(self)
        g = QGroupBox("Document Open password")
        f = QFormLayout(g)
        self.user = QLineEdit(cur.get("user_pw", ""), echoMode=QLineEdit.Password)
        self.user2 = QLineEdit(cur.get("user_pw", ""), echoMode=QLineEdit.Password)
        f.addRow("&Password:", self.user)
        f.addRow("&Confirm:", self.user2)
        v.addWidget(g)
        g = QGroupBox("Permissions password")
        f = QFormLayout(g)
        self.owner = QLineEdit(cur.get("owner_pw", ""), echoMode=QLineEdit.Password)
        self.owner2 = QLineEdit(cur.get("owner_pw", ""), echoMode=QLineEdit.Password)
        f.addRow("P&assword:", self.owner)
        f.addRow("C&onfirm:", self.owner2)
        self.print_ = QComboBox()
        self.print_.addItems(["Allowed", "Low resolution only", "Not allowed"])
        self.modify = QCheckBox("Allow changes to the document")
        self.copy = QCheckBox("Allow copying of text, pictures and other content")
        self.annot = QCheckBox("Allow commenting and filling in forms")
        for c in (self.modify, self.copy, self.annot):
            c.setChecked(True)
        f.addRow("Printing:", self.print_)
        f.addRow(self.modify)
        f.addRow(self.copy)
        f.addRow(self.annot)
        v.addWidget(g)
        f = QFormLayout()
        self.enc = QComboBox()
        self.enc.addItem("256-bit AES", pymupdf.PDF_ENCRYPT_AES_256)
        self.enc.addItem("128-bit AES", pymupdf.PDF_ENCRYPT_AES_128)
        self.enc.addItem("128-bit RC4", pymupdf.PDF_ENCRYPT_RC4_128)
        f.addRow("Encryption level:", self.enc)
        v.addLayout(f)
        self.msg = QLabel("Both the Permissions password and the Document Open password will allow you to open the document.")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        bb = _bb(self, "&Apply")
        bb.accepted.disconnect()
        bb.accepted.connect(self._check)
        v.addWidget(bb)

    def _check(self):
        if self.user.text() != self.user2.text() or self.owner.text() != self.owner2.text():
            self.msg.setText("<b style='color:#c00'>Invalid password: the confirmation does not match.</b>")
            return
        if self.user.text() and self.user.text() == self.owner.text():
            self.msg.setText("<b style='color:#c00'>The Document Open password and Permissions password cannot be the same.</b>")
            return
        self.accept()

    def result_options(self):
        if not self.user.text() and not self.owner.text():
            return None
        perm = 0
        p = self.print_.currentIndex()
        if p == 0:
            perm |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
        elif p == 1:
            perm |= pymupdf.PDF_PERM_PRINT
        if self.modify.isChecked():
            perm |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE
        if self.copy.isChecked():
            perm |= pymupdf.PDF_PERM_COPY | pymupdf.PDF_PERM_ACCESSIBILITY
        if self.annot.isChecked():
            perm |= pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM
        owner = self.owner.text() or self.user.text() + "-owner"
        return {"encryption": self.enc.currentData(), "user_pw": self.user.text(), "owner_pw": owner,
                "permissions": perm}


class ReduceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reduce File Size")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("You can reduce the file size of documents that contain many pictures by lowering picture quality:"))
        self.q = QComboBox()
        self.q.addItem("High quality (300 dpi)", (300, 85))
        self.q.addItem("Balanced (150 dpi)", (150, 75))
        self.q.addItem("Compact (96 dpi)", (96, 55))
        self.q.setCurrentIndex(1)
        v.addWidget(self.q)
        self.gray = QCheckBox("Convert pictures to grayscale")
        self.fonts = QCheckBox("Subset embedded fonts")
        self.fonts.setChecked(True)
        v.addWidget(self.gray)
        v.addWidget(self.fonts)
        v.addWidget(_bb(self, "&Apply"))


class PropertiesDialog(QDialog):
    def __init__(self, doc, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self.resize(520, 440)
        self.doc = doc
        md = doc.metadata or {}
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs)
        w = QWidget()
        f = QFormLayout(w)
        self.fields = {}
        for k, label in (("title", "&Title:"), ("author", "&Author:"), ("subject", "&Subject:"),
                         ("keywords", "&Keywords:"), ("creator", "&Creator:"), ("producer", "&Producer:")):
            le = QLineEdit(md.get(k, "") or "")
            self.fields[k] = le
            f.addRow(label, le)
        tabs.addTab(w, "Description")
        w = QWidget()
        f = QFormLayout(w)
        size = Path(path).stat().st_size if path and Path(path).exists() else 0
        p0 = doc[0].rect if len(doc) else pymupdf.Rect()
        fonts = set()
        for i in range(min(len(doc), 50)):
            for fo in doc.get_page_fonts(i):
                fonts.add(fo[3])
        for label, val in (("File:", path or "(not saved)"), ("File size:", f"{size / 1024:.1f} KB"),
                           ("Pages:", str(len(doc))), ("PDF format:", md.get("format", "")),
                           ("Page size:", f"{p0.width / 72 * 25.4:.0f} × {p0.height / 72 * 25.4:.0f} mm"),
                           ("Encryption:", md.get("encryption") or "None"),
                           ("Created:", md.get("creationDate", "")), ("Modified:", md.get("modDate", "")),
                           ("Fonts:", ", ".join(sorted(fonts))[:300] or "None"),
                           ("Text layer:", "yes" if any(doc[i].get_text("text").strip() for i in range(min(5, len(doc)))) else "no")):
            l = QLabel(val)
            l.setWordWrap(True)
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
            f.addRow(label, l)
        tabs.addTab(w, "Information")
        v.addWidget(_bb(self))

    def apply(self):
        md = dict(self.doc.metadata or {})
        for k, le in self.fields.items():
            md[k] = le.text()
        self.doc.set_metadata({k: v for k, v in md.items() if k in ("title", "author", "subject", "keywords",
                                                                     "creator", "producer", "creationDate", "modDate")})


class DeleteDataDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Objects and Data")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Select objects and data to delete:"))
        self.hidden = QCheckBox("&Hidden text and objects")
        self.comments = QCheckBox("&Comments and markup")
        self.links = QCheckBox("&Links, media, actions, scripts, and form data")
        self.bookmarks = QCheckBox("B&ookmarks")
        self.attachments = QCheckBox("A&ttachments")
        self.metadata = QCheckBox("&Metadata")
        for c in (self.hidden, self.comments, self.links, self.bookmarks, self.attachments, self.metadata):
            c.setChecked(c is not self.hidden)
            v.addWidget(c)
        v.addWidget(_bb(self, "&Apply"))


class WatermarkDialog(QDialog):
    def __init__(self, count, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Watermark")
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.text = QLineEdit("CONFIDENTIAL")
        f.addRow("Te&xt:", self.text)
        self.size = QSpinBox(minimum=6, maximum=300, value=60)
        f.addRow("&Font size:", self.size)
        self.color = QColor(200, 30, 30)
        self.color_btn = QPushButton("Color...")
        self.color_btn.setStyleSheet(f"background:{self.color.name()};color:white")
        self.color_btn.clicked.connect(self._pick)
        f.addRow("Color:", self.color_btn)
        self.rot = QSpinBox(minimum=-180, maximum=180, value=45, suffix="º")
        f.addRow("&Rotate:", self.rot)
        self.opacity = QSpinBox(minimum=0, maximum=95, value=70, suffix="%")
        f.addRow("&Transparency:", self.opacity)
        self.pos = QComboBox()
        self.pos.addItems(["Center", "Top", "Bottom"])
        f.addRow("Position:", self.pos)
        self.behind = QComboBox()
        self.behind.addItems(["In front of page content", "Behind page content"])
        f.addRow("&Visibility:", self.behind)
        v.addLayout(f)
        self.scope = PageScope(count, current)
        v.addWidget(self.scope)
        v.addWidget(_bb(self, "&Save and Add"))

    def _pick(self):
        c = QColorDialog.getColor(self.color, self)
        if c.isValid():
            self.color = c
            self.color_btn.setStyleSheet(f"background:{c.name()};color:white")


class HeaderFooterDialog(QDialog):
    HELP = "Tokens: <<page>> page number, <<pages>> page count, <<date>>, <<time>>, <<bates>> Bates number."

    def __init__(self, count, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Header and Footer")
        v = QVBoxLayout(self)
        g = QGroupBox("Text")
        f = QFormLayout(g)
        self.cells = {}
        for k, label in (("tl", "Header left:"), ("tc", "Header center:"), ("tr", "Header right:"),
                         ("bl", "Footer left:"), ("bc", "Footer center:"), ("br", "Footer right:")):
            le = QLineEdit()
            self.cells[k] = le
            f.addRow(label, le)
        self.cells["bc"].setText("Page <<page>> of <<pages>>")
        v.addWidget(g)
        h = QHBoxLayout()
        for t, tok in (("&Page Number", "<<page>>"), ("Bates Numbe&r", "<<bates>>"), ("Date/&Time", "<<date>>")):
            b = QPushButton(t)
            b.clicked.connect(lambda _=False, tok=tok: self._insert(tok))
            h.addWidget(b)
        v.addLayout(h)
        lab = QLabel(self.HELP)
        lab.setWordWrap(True)
        v.addWidget(lab)
        f = QFormLayout()
        self.start = QSpinBox(minimum=0, maximum=10 ** 7, value=1)
        f.addRow("&Start page numbers at:", self.start)
        self.bates_prefix = QLineEdit("DOC")
        self.bates_start = QSpinBox(minimum=0, maximum=10 ** 8, value=1)
        self.bates_digits = QSpinBox(minimum=1, maximum=12, value=6)
        f.addRow("Bates prefix:", self.bates_prefix)
        f.addRow("Bates start / digits:", self._row(self.bates_start, self.bates_digits))
        self.size = QSpinBox(minimum=5, maximum=72, value=10)
        f.addRow("&Font size:", self.size)
        self.margin = QSpinBox(minimum=0, maximum=200, value=24, suffix=" pt")
        f.addRow("Margin:", self.margin)
        v.addLayout(f)
        self.scope = PageScope(count, current)
        v.addWidget(self.scope)
        v.addWidget(_bb(self, "Save an&d Add"))
        self._last = self.cells["bc"]
        for le in self.cells.values():
            le.installEventFilter(self)

    def _row(self, *ws):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in ws:
            h.addWidget(x)
        return w

    def eventFilter(self, o, e):
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.FocusIn and isinstance(o, QLineEdit):
            self._last = o
        return False

    def _insert(self, tok):
        self._last.insert(tok)

    def render(self, text, idx, total):
        now = datetime.datetime.now()
        n = idx + self.start.value()
        bates = f"{self.bates_prefix.text()}{str(self.bates_start.value() + idx).zfill(self.bates_digits.value())}"
        return (text.replace("<<page>>", str(n)).replace("<<pages>>", str(total + self.start.value() - 1))
                .replace("<<date>>", now.strftime("%d/%m/%Y")).replace("<<time>>", now.strftime("%H:%M"))
                .replace("<<bates>>", bates))


class TextStampDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Text Stamp")
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.text = QLineEdit("REVIEWED <<date>>")
        f.addRow("Stamp &text:", self.text)
        self.color = QComboBox()
        for n, c in (("Red", (0.8, 0.1, 0.1)), ("Blue", (0.1, 0.3, 0.8)), ("Green", (0.1, 0.55, 0.2)), ("Black", (0, 0, 0))):
            self.color.addItem(n, c)
        f.addRow("&Color:", self.color)
        self.size = QSpinBox(minimum=6, maximum=72, value=16)
        f.addRow("Font &size:", self.size)
        v.addLayout(f)
        v.addWidget(QLabel("<<date>> and <<time>> are replaced when the stamp is placed."))
        v.addWidget(_bb(self))


class LinkDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link Editor")
        v = QVBoxLayout(self)
        self.web = QRadioButton("Link to a &web page or file:")
        self.url = QLineEdit("https://")
        self.pg = QRadioButton("Link to a &page in this document:")
        self.page = QSpinBox(minimum=1, maximum=max(1, count))
        self.web.setChecked(True)
        v.addWidget(self.web)
        v.addWidget(self.url)
        v.addWidget(self.pg)
        v.addWidget(self.page)
        v.addWidget(_bb(self))

    def link(self, rect):
        if self.web.isChecked():
            return {"kind": pymupdf.LINK_URI, "from": rect, "uri": self.url.text().strip()}
        return {"kind": pymupdf.LINK_GOTO, "from": rect, "page": self.page.value() - 1, "to": pymupdf.Point(0, 0)}


class EditTextDialog(QDialog):
    def __init__(self, text, size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Text")
        self.resize(480, 260)
        v = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        v.addWidget(self.edit)
        f = QFormLayout()
        self.size = QDoubleSpinBox(minimum=4, maximum=96, value=size, singleStep=0.5)
        f.addRow("Font size:", self.size)
        v.addLayout(f)
        v.addWidget(QLabel("The original text in the selected rectangle is replaced by the new text."))
        v.addWidget(_bb(self))


class AnnotationDialog(QDialog):
    def __init__(self, annot, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Properties")
        v = QVBoxLayout(self)
        info = annot.info
        f = QFormLayout()
        self.author = QLineEdit(info.get("title", ""))
        f.addRow("Author:", self.author)
        v.addLayout(f)
        self.content = QPlainTextEdit(info.get("content", ""))
        v.addWidget(self.content)
        self.op = QSlider(Qt.Horizontal, minimum=10, maximum=100, value=int((annot.opacity if annot.opacity >= 0 else 1) * 100))
        f2 = QFormLayout()
        f2.addRow("Opacity:", self.op)
        v.addLayout(f2)
        v.addWidget(_bb(self))
