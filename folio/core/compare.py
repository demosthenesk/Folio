"""Document comparison engine (Compare Documents)."""
import datetime as _dt
import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pymupdf
from PIL import Image

from . import ocr
from .document import as_pdf_path, kind_of, load_pages

VIEW_DPI = 150
_PUNCT = re.compile(r"[\W_]+", re.UNICODE)
_NUMBERING = re.compile(r"^(\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxlcIVXLC]{1,5}[.)]|[•·▪◦\-–—*])$")


@dataclass
class Token:
    text: str
    page: int
    bbox: tuple  # px at VIEW_DPI
    category: str = "body"  # body | header | numbering
    par_end: bool = False
    line_end: bool = False


@dataclass
class DocText:
    path: str
    tokens: List[Token] = field(default_factory=list)
    images: List[Image.Image] = field(default_factory=list)  # page images at VIEW_DPI
    used_ocr: bool = False

    @property
    def name(self):
        return Path(self.path).name


@dataclass
class Difference:
    id: int
    kind: str  # deleted | added | changed
    text1: str
    text2: str
    page1: int
    rects1: list
    page2: int
    rects2: list
    category: str = "body"
    ignored: bool = False

    def label(self):
        if self.kind == "deleted":
            return f"[Text deleted] {self.text1}"
        if self.kind == "added":
            return f"[Text added] {self.text2}"
        return f'"{self.text1}" to "{self.text2}"'


def _norm(t, punctuation):
    t = unicodedata.normalize("NFC", t)
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    if not punctuation:
        t = _PUNCT.sub("", t)
    return t


def extract(path, langs=None, mode="auto", progress=None, cancel=None) -> DocText:
    """Get word tokens (with positions) and page images for a document."""
    dt = DocText(str(path))
    k = kind_of(path)
    if k not in ("image",):
        src = as_pdf_path(path)
    else:
        src = str(path)
    pages_view = list(load_pages(src, dpi=VIEW_DPI, with_text=(mode != "ocr")))
    n = len(pages_view)
    for i, lp in enumerate(pages_view):
        if cancel and cancel():
            raise RuntimeError("Cancelled")
        if progress:
            progress(i, n, f"{Path(path).name}: page {i + 1} of {n}")
        dt.images.append(lp.image)
        layer = lp.text_layer
        use_layer = layer is not None and (mode == "text" or (mode == "auto" and sum(1 for _ in layer.words()) >= 5))
        if not use_layer:
            # OCR at 300 dpi for accuracy, then scale coordinates to the view resolution
            hi = next(load_pages(src, dpi=300, page_indices=[i], with_text=False)).image if k != "image" else lp.image
            res = ocr.recognize(hi, langs)
            f = lp.image.width / float(hi.width)
            for b in res.blocks:
                for l in b.lines:
                    for w in l.words:
                        x, y, ww, hh = w.bbox
                        w.bbox = (int(x * f), int(y * f), max(1, int(ww * f)), max(1, int(hh * f)))
            layer = res
            dt.used_ocr = True
        _tokens_from_layer(dt, layer, i, lp.image.height)
    _mark_headers(dt)
    return dt


def _tokens_from_layer(dt, layer, page, height):
    for b in layer.blocks:
        if b.kind == "picture":
            continue
        for li, l in enumerate(b.lines):
            for wi, w in enumerate(l.words):
                t = Token(w.text, page, w.bbox)
                if wi == 0 and _NUMBERING.match(w.text):
                    t.category = "numbering"
                t.line_end = wi == len(l.words) - 1
                t.par_end = t.line_end and li == len(b.lines) - 1
                dt.tokens.append(t)


def _mark_headers(dt: DocText):
    """Lines that sit in the top/bottom 8% of the page and repeat across pages are headers/footers."""
    if not dt.images:
        return
    lines = {}
    cur = []
    for t in dt.tokens:
        cur.append(t)
        if t.line_end:
            h = dt.images[t.page].height
            cy = t.bbox[1] + t.bbox[3] / 2
            if cy < h * 0.08 or cy > h * 0.92:
                key = re.sub(r"\d+", "#", " ".join(x.text for x in cur)).lower()
                lines.setdefault(key, []).append(list(cur))
            cur = []
    pages = len(dt.images)
    for key, groups in lines.items():
        repeated = len({g[0].page for g in groups}) >= 2 or pages == 1
        pagenum = re.fullmatch(r"(page )?#( of #)?|- # -|#", key.strip()) is not None
        if repeated or pagenum:
            for g in groups:
                for t in g:
                    t.category = "header"


def _lev1(a, b):
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= (1 - 2.0 / max(1, len(a) + len(b))) - 1e-9


def _merge_rects(tokens):
    out = []
    for t in tokens:
        x, y, w, h = t.bbox
        if out and out[-1][0] == t.page and abs(out[-1][1][1] - y) < h * 0.6:
            px, py, pw, ph = out[-1][1]
            nx, ny = min(px, x), min(py, y)
            out[-1] = (t.page, (nx, ny, max(px + pw, x + w) - nx, max(py + ph, y + h) - ny))
        else:
            out.append((t.page, (x, y, w, h)))
    return out


def compare(d1: DocText, d2: DocText, punctuation=False, one_letter=False, headers=True):
    def seq(d):
        toks, keys = [], []
        for t in d.tokens:
            k = _norm(t.text, punctuation)
            if k:
                toks.append(t)
                keys.append(k)
        return toks, keys

    t1, k1 = seq(d1)
    t2, k2 = seq(d2)
    sm = difflib.SequenceMatcher(None, k1, k2, autojunk=False)
    diffs = []
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            continue
        A, B = t1[a1:a2], t2[b1:b2]
        if not one_letter and op == "replace" and len(A) == 1 and len(B) == 1:
            ka, kb = k1[a1], k2[b1]
            if len(ka) > 1 and len(kb) > 1 and _lev1(ka, kb) and sum(x != y for x, y in zip(ka, kb)) + abs(len(ka) - len(kb)) <= 1:
                continue
        cats = {t.category for t in A + B}
        cat = "header" if cats == {"header"} else ("numbering" if cats == {"numbering"} else "body")
        if not headers and cat == "header":
            continue
        kind = {"delete": "deleted", "insert": "added", "replace": "changed"}[op]
        r1 = _merge_rects(A)
        r2 = _merge_rects(B)
        # anchor for missing side: the token just before the gap
        if not r1 and t1:
            anc = t1[min(a1, len(t1) - 1)]
            r1 = [(anc.page, (anc.bbox[0], anc.bbox[1], 3, anc.bbox[3]))]
        if not r2 and t2:
            anc = t2[min(b1, len(t2) - 1)]
            r2 = [(anc.page, (anc.bbox[0], anc.bbox[1], 3, anc.bbox[3]))]
        diffs.append(Difference(len(diffs), kind, " ".join(t.text for t in A), " ".join(t.text for t in B),
                                r1[0][0] if r1 else 0, r1, r2[0][0] if r2 else 0, r2, cat))
    return diffs


def summary(diffs):
    act = [d for d in diffs if not d.ignored]
    return {"body": sum(d.category == "body" for d in act), "header": sum(d.category == "header" for d in act),
            "numbering": sum(d.category == "numbering" for d in act), "total": len(act)}


# ---------------------------------------------------------------- saving results
def _px_to_pt(rect, page):
    x, y, w, h = rect
    f = 72.0 / VIEW_DPI
    r = pymupdf.Rect(x * f, y * f, (x + w) * f, (y + h) * f)
    return r * page.derotation_matrix if page.rotation else r


def _source_pdf(d: DocText):
    if kind_of(d.path) == "image":
        doc = pymupdf.open()
        for im in d.images:
            import io
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=88)
            pg = doc.new_page(width=im.width * 72 / VIEW_DPI, height=im.height * 72 / VIEW_DPI)
            pg.insert_image(pg.rect, stream=buf.getvalue())
        return doc
    return pymupdf.open(as_pdf_path(d.path))


def save_pdf_with_comments(diffs, d: DocText, side: int, out):
    doc = _source_pdf(d)
    for df in diffs:
        if df.ignored:
            continue
        rects = df.rects1 if side == 1 else df.rects2
        present = (df.kind != "added") if side == 1 else (df.kind != "deleted")
        if not rects:
            continue
        msg = df.label()
        page = doc[rects[0][0]]
        if present:
            quads = [_px_to_pt(r, doc[p]) for p, r in rects if p == rects[0][0]]
            a = page.add_highlight_annot(quads)
            a.set_colors(stroke=(1, 0.6, 0.6) if df.kind == "deleted" else (1, 0.85, 0.3))
        else:
            r = _px_to_pt(rects[0][1], page)
            a = page.add_caret_annot(r.tl) if hasattr(page, "add_caret_annot") else page.add_text_annot(r.tl, msg)
        a.set_info(title="Folio Compare", content=msg)
        a.update()
    doc.save(out, garbage=3, deflate=True)
    return out


def _add_run(par, text, mode=None, rid=[1000]):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    r = OxmlElement("w:r")
    t = OxmlElement("w:delText" if mode == "del" else "w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    if mode:
        wrap = OxmlElement("w:del" if mode == "del" else "w:ins")
        rid[0] += 1
        wrap.set(qn("w:id"), str(rid[0]))
        wrap.set(qn("w:author"), "Folio Compare")
        wrap.set(qn("w:date"), _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        wrap.append(r)
        par._p.append(wrap)
    else:
        par._p.append(r)


def save_docx_track_changes(d1: DocText, d2: DocText, out):
    """Modified document as DOCX where the differences against the original are tracked changes."""
    from docx import Document
    doc = Document()
    a = [t.text for t in d1.tokens]
    b = [t.text for t in d2.tokens]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    par = doc.add_paragraph()

    def emit(tok, mode):
        nonlocal par
        _add_run(par, tok.text + " ", mode)
        if tok.par_end and mode != "del":
            par = doc.add_paragraph()

    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            for t in d2.tokens[b1:b2]:
                emit(t, None)
        else:
            for t in d1.tokens[a1:a2]:
                emit(t, "del")
            for t in d2.tokens[b1:b2]:
                emit(t, "ins")
    doc.save(out)
    return out


def save_report(diffs, d1: DocText, d2: DocText, out, options):
    """Comparison report (DOCX, or HTML/TXT by extension)."""
    s = summary(diffs)
    yesno = lambda v: "Yes" if v else "No"
    head = [f"Original document: {d1.name}", f"Modified document: {d2.name}", "",
            "Settings:", f"Find differences in punctuation: {yesno(options.get('punctuation'))}",
            f"Find one-letter differences: {yesno(options.get('one_letter'))}",
            f"Export differences in headers and footers: {yesno(options.get('headers', True))}", "",
            "Differences:", f"Body text: {s['body']}", f"Headers and footers: {s['header']}",
            f"Numbering: {s['numbering']}", f"Total: {s['total']}"]
    rows = [(str(i + 1), f"{df.page1 + 1}", df.text1, f"{df.page2 + 1}", df.text2,
             {"deleted": "Deleted", "added": "Added", "changed": "Changed"}[df.kind])
            for i, df in enumerate(d for d in diffs if not d.ignored)]
    ext = Path(out).suffix.lower()
    if ext == ".docx":
        from docx import Document
        doc = Document()
        doc.add_heading("Comparison report", 0)
        for h in head:
            doc.add_paragraph(h)
        t = doc.add_table(rows=1, cols=6)
        t.style = "Table Grid"
        for c, txt in zip(t.rows[0].cells, ["#", "Page", "Original", "Page", "Modified", "Type"]):
            c.text = txt
        for r in rows:
            cells = t.add_row().cells
            for c, txt in zip(cells, r):
                c.text = txt
        doc.add_paragraph(f"This document was created by Folio Compare ({_dt.date.today().isoformat()}).")
        doc.save(out)
    elif ext in (".html", ".htm"):
        import html as H
        body = "".join(f"<p>{H.escape(h)}</p>" for h in head)
        body += "<table border=1 cellpadding=4 style='border-collapse:collapse'><tr><th>#</th><th>Page</th><th>Original</th><th>Page</th><th>Modified</th><th>Type</th></tr>"
        body += "".join("<tr>" + "".join(f"<td>{H.escape(c)}</td>" for c in r) + "</tr>" for r in rows) + "</table>"
        Path(out).write_text(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Comparison report</title></head><body><h1>Comparison report</h1>{body}</body></html>", "utf-8")
    else:
        Path(out).write_text("Comparison report\n\n" + "\n".join(head) + "\n\n" +
                             "\n".join("\t".join(r) for r in rows) + "\n", "utf-8")
    return out
