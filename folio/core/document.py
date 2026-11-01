"""Loading source files into page images, and the OCR project model (.folio project files)."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pymupdf
from PIL import Image

from . import paths
from .ocr import Block, Line, OcrPage, Word, build_table, union, words_to_lines

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".pnm",
             ".pbm", ".pgm", ".ppm", ".jp2", ".j2k", ".pcx", ".dcx", ".ico"}
PDF_EXT = {".pdf", ".xps", ".oxps", ".epub", ".fb2", ".cbz", ".mobi"}  # all opened through MuPDF
OFFICE_EXT = {".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp",
              ".vsd", ".vsdx", ".htm", ".html", ".csv"}
TEXT_EXT = {".txt", ".md", ".log"}
DJVU_EXT = {".djvu", ".djv"}
PROJECT_EXT = ".folio"

OPEN_FILTER = ("All supported files (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.pnm *.jp2 "
               "*.pcx *.xps *.epub *.djvu *.doc *.docx *.odt *.rtf *.txt *.xls *.xlsx *.ods *.ppt *.pptx *.odp *.htm *.html);;"
               "PDF files (*.pdf);;Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.pnm *.jp2 *.pcx);;"
               "Office documents (*.doc *.docx *.odt *.rtf *.xls *.xlsx *.ods *.ppt *.pptx *.odp);;"
               "Text files (*.txt);;All files (*)")
IMAGE_FILTER = "Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.pnm *.jp2 *.pcx);;All files (*)"


class LoadError(RuntimeError):
    pass


@dataclass
class LoadedPage:
    image: Image.Image
    source: str
    index: int
    text_layer: Optional[OcrPage] = None


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in OFFICE_EXT:
        return "office"
    if ext in TEXT_EXT:
        return "text"
    if ext in DJVU_EXT:
        return "djvu"
    if ext == PROJECT_EXT:
        return "project"
    return "unknown"


def parse_page_range(spec: str, count: int):
    """'1,3,5-7' -> zero-based indices; empty -> all pages."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(count))
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else count
            if a < 1 or b > count or a > b:
                raise ValueError(f"Invalid page range: {part}")
            out.extend(range(a - 1, b))
        else:
            n = int(part)
            if n < 1 or n > count:
                raise ValueError(f"Page not found: {n}")
            out.append(n - 1)
    return out


def office_to_pdf(path) -> str:
    """Convert an office document to PDF using LibreOffice if available."""
    soffice = paths.which_external("soffice") or paths.which_external("libreoffice")
    if not soffice:
        raise LoadError("Opening this format requires LibreOffice (sudo apt install libreoffice).")
    out = tempfile.mkdtemp(prefix="folio-conv-")
    p = paths.run_external([soffice, "--headless", "--convert-to", "pdf", "--outdir", out, str(path)],
                           wait=True, capture_output=True, timeout=300)
    pdf = Path(out) / (Path(path).stem + ".pdf")
    if not pdf.exists():
        raise LoadError(f"LibreOffice could not convert {Path(path).name}: {p.stderr.decode(errors='replace')[:300]}")
    return str(pdf)


def text_to_pdf(path) -> str:
    text = Path(path).read_text("utf-8", errors="replace")
    return str(text_string_to_pdf(text))


def text_string_to_pdf(text, out=None):
    doc = pymupdf.open()
    font = unicode_font_file()
    story_lines = text.splitlines() or [""]
    w, h, m = 595, 842, 56
    fs, lh = 10.5, 14
    page = None
    y = h
    fobj = pymupdf.Font(fontfile=font) if font else pymupdf.Font("helv")
    for raw in story_lines:
        # simple wrap
        chunks = []
        cur = ""
        for word in raw.split(" "):
            t = (cur + " " + word) if cur else word
            if fobj.text_length(t, fs) > w - 2 * m and cur:
                chunks.append(cur)
                cur = word
            else:
                cur = t
        chunks.append(cur)
        for c in chunks:
            if page is None or y > h - m:
                page = doc.new_page(width=w, height=h)
                if font:
                    page.insert_font(fontname="F0", fontfile=font)
                y = m + fs
            page.insert_text((m, y), c, fontsize=fs, fontname="F0" if font else "helv")
            y += lh
    out = out or tempfile.mktemp(prefix="folio-txt-", suffix=".pdf")
    doc.save(out)
    return out


def unicode_font_file():
    for p in [paths.bundled("usr/share/fonts/DejaVuSans.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]:
        if p and Path(p).exists():
            return str(p)
    return None


def djvu_to_pdf(path) -> str:
    ddjvu = paths.which_external("ddjvu")
    if not ddjvu:
        raise LoadError("Opening DjVu files requires djvulibre-bin (sudo apt install djvulibre-bin).")
    out = tempfile.mktemp(prefix="folio-djvu-", suffix=".pdf")
    paths.run_external([ddjvu, "-format=pdf", str(path), out], wait=True, capture_output=True, timeout=600)
    if not Path(out).exists():
        raise LoadError("Could not convert the DjVu file.")
    return out


def as_pdf_path(path) -> str:
    """Return a path to a MuPDF-openable document for any supported input."""
    k = kind_of(path)
    if k == "pdf":
        return str(path)
    if k == "office":
        return office_to_pdf(path)
    if k == "text":
        return text_to_pdf(path)
    if k == "djvu":
        return djvu_to_pdf(path)
    if k == "image":
        return str(path)
    raise LoadError(f"The format of the file {Path(path).name} is not supported.")


def pdf_text_layer(page: "pymupdf.Page", scale: float) -> Optional[OcrPage]:
    words = page.get_text("words")
    if not words:
        return None
    w = int(page.rect.width * scale)
    h = int(page.rect.height * scale)
    res = OcrPage(w, h, source="pdftext")
    blocks = {}
    for x0, y0, x1, y1, t, bno, lno, wno in words:
        blocks.setdefault(bno, []).append(Word(t, (int(x0 * scale), int(y0 * scale),
                                                   max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale)))))
    for bno in sorted(blocks):
        lines = words_to_lines(blocks[bno])
        res.blocks.append(Block(lines=lines, bbox=union([l.bbox for l in lines])))
    return res


def pdf_has_text(path, max_pages=5):
    try:
        d = pymupdf.open(path)
        n = min(len(d), max_pages)
        return any(d[i].get_text("text").strip() for i in range(n))
    except Exception:
        return False


def page_count(path):
    k = kind_of(path)
    if k == "image":
        try:
            with Image.open(path) as im:
                return getattr(im, "n_frames", 1)
        except Exception:
            return 1
    try:
        return len(pymupdf.open(as_pdf_path(path)))
    except Exception:
        return 0


def load_pages(path, dpi=300, page_indices=None, with_text=True):
    """Yield LoadedPage objects for every page of a file (images, PDFs, office docs...)."""
    path = str(path)
    k = kind_of(path)
    if k == "image":
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            idx = page_indices if page_indices is not None else range(n)
            for i in idx:
                im.seek(i)
                frame = im.copy()
                if frame.mode not in ("L", "RGB", "1"):
                    frame = frame.convert("RGB")
                if frame.mode == "1":
                    frame = frame.convert("L")
                d = im.info.get("dpi", (0, 0))
                fd = int(round(d[0])) if d and d[0] else 0
                frame.info["dpi"] = (fd or 300, fd or 300)
                if not fd and max(frame.size) < 1600:
                    frame.info["dpi"] = (150, 150)
                yield LoadedPage(frame, path, i)
        return
    pdfp = as_pdf_path(path)
    doc = pymupdf.open(pdfp)
    if doc.needs_pass:
        raise LoadError("PASSWORD")
    idx = page_indices if page_indices is not None else range(len(doc))
    scale = dpi / 72.0
    for i in idx:
        page = doc[i]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img.info["dpi"] = (dpi, dpi)
        tl = pdf_text_layer(page, scale) if with_text else None
        yield LoadedPage(img, path, i, tl)


def open_pdf(path, password=None):
    doc = pymupdf.open(as_pdf_path(path))
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise LoadError("PASSWORD")
    return doc


# ---------------------------------------------------------------- OCR project model
AREA_KINDS = ("text", "table", "picture", "background", "barcode", "recognition")


@dataclass
class Area:
    kind: str
    rect: tuple  # x, y, w, h

    def to_list(self):
        return [self.kind, list(self.rect)]


@dataclass
class ProjectPage:
    id: str
    image_file: str
    name: str = ""
    areas: List[Area] = field(default_factory=list)
    ocr: Optional[OcrPage] = None
    text_override: Optional[str] = None
    verified: bool = False
    analyzed: bool = False
    _img: Optional[Image.Image] = None

    @property
    def recognized(self):
        return self.ocr is not None

    def text(self, keep_line_breaks=True):
        if self.text_override is not None:
            return self.text_override
        return self.ocr.text(keep_line_breaks) if self.ocr else ""


class Project:
    """An OCR project: a set of page images with areas and recognized text.

    Saved as a zip (`*.folio`) containing project.json and the page images."""

    def __init__(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="folio-project-"))
        self.pages: List[ProjectPage] = []
        self.path: Optional[str] = None
        self.languages: Optional[List[str]] = None
        self.dirty = False

    def __del__(self):
        try:
            shutil.rmtree(self.workdir, ignore_errors=True)
        except Exception:
            pass

    # -- pages
    def image(self, page: ProjectPage) -> Image.Image:
        if page._img is None:
            im = Image.open(self.workdir / page.image_file)
            im.load()
            dpi = im.info.get("dpi", (300, 300))
            im.info["dpi"] = (int(round(dpi[0])) or 300, int(round(dpi[1])) or 300)
            page._img = im
        return page._img

    def set_image(self, page: ProjectPage, img: Image.Image, keep_ocr=False):
        dpi = img.info.get("dpi", (300, 300))
        img.save(self.workdir / page.image_file, "PNG", dpi=dpi)
        img.info["dpi"] = dpi
        page._img = img
        if not keep_ocr:
            page.ocr = None
            page.text_override = None
            page.areas = []
            page.analyzed = False
            page.verified = False
        self.dirty = True

    def add_image(self, img: Image.Image, name="", text_layer: Optional[OcrPage] = None, index=None):
        pid = uuid.uuid4().hex[:12]
        fn = f"page_{pid}.png"
        dpi = img.info.get("dpi", (300, 300))
        img.save(self.workdir / fn, "PNG", dpi=dpi)
        p = ProjectPage(pid, fn, name=name)
        p._img = img
        if text_layer is not None:
            p.ocr = text_layer
            p.areas = [Area("text", b.bbox) for b in text_layer.blocks]
            p.analyzed = True
        if index is None:
            self.pages.append(p)
        else:
            self.pages.insert(index, p)
        self.dirty = True
        return p

    def remove_pages(self, pages):
        ids = {p.id for p in pages}
        self.pages = [p for p in self.pages if p.id not in ids]
        self.dirty = True

    # -- persistence
    def save(self, path):
        meta = {"format": "folio-project", "version": 1, "languages": self.languages, "pages": []}
        for p in self.pages:
            meta["pages"].append({
                "id": p.id, "image": p.image_file, "name": p.name,
                "areas": [a.to_list() for a in p.areas],
                "ocr": p.ocr.to_dict() if p.ocr else None,
                "text_override": p.text_override, "verified": p.verified, "analyzed": p.analyzed})
        tmp = str(path) + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(meta, ensure_ascii=False))
            for p in self.pages:
                z.write(self.workdir / p.image_file, p.image_file, compress_type=zipfile.ZIP_STORED)
        os.replace(tmp, path)
        self.path = str(path)
        self.dirty = False

    @classmethod
    def load(cls, path):
        pr = cls()
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("project.json").decode("utf-8"))
            if meta.get("format") != "folio-project":
                raise LoadError("Not a Folio OCR project.")
            z.extractall(pr.workdir)
        pr.languages = meta.get("languages")
        for d in meta["pages"]:
            p = ProjectPage(d["id"], d["image"], d.get("name", ""),
                            [Area(k, tuple(r)) for k, r in d.get("areas", [])],
                            OcrPage.from_dict(d["ocr"]) if d.get("ocr") else None,
                            d.get("text_override"), d.get("verified", False), d.get("analyzed", False))
            pr.pages.append(p)
        pr.path = str(path)
        return pr


# ---------------------------------------------------------------- recognition helpers on pages
def area_contains(rect, bbox, frac=0.5):
    x, y, w, h = rect
    bx, by, bw, bh = bbox
    ix = max(0, min(x + w, bx + bw) - max(x, bx))
    iy = max(0, min(y + h, by + bh) - max(y, by))
    return bw * bh > 0 and (ix * iy) / float(bw * bh) >= frac


def recognize_page(project: Project, page: ProjectPage, langs, psm="auto", fast=None, area_rects=None):
    """OCR a project page honoring its areas (or analysing layout automatically when none)."""
    from . import ocr
    img = project.image(page)
    areas = page.areas
    if area_rects is not None:
        areas = [a for a in page.areas if a.rect in area_rects]
    if not areas:
        res = ocr.recognize(img, langs, psm=psm, fast=fast)
        page.ocr = res
        page.areas = [Area(b.kind if b.kind == "picture" else "text", b.bbox) for b in res.blocks]
        page.analyzed = True
        page.text_override = None
        return res
    # recognize each area separately, in reading order of the areas list
    result = page.ocr if (area_rects is not None and page.ocr) else ocr.OcrPage(img.width, img.height)
    if area_rects is not None and page.ocr:
        result.blocks = [b for b in result.blocks if not any(area_contains(r, b.bbox, 0.6) for r in area_rects)]
    for a in areas:
        if a.kind in ("picture", "background"):
            result.blocks.append(Block(bbox=a.rect, kind="picture"))
            continue
        x, y, w, h = [int(v) for v in a.rect]
        if w < 4 or h < 4:
            continue
        sub = img.crop((x, y, x + w, y + h))
        sub.info["dpi"] = img.info.get("dpi", (300, 300))
        r = ocr.recognize(sub, langs, psm="block" if a.kind in ("text", "barcode") and h < 200 else
                          ("sparse" if a.kind == "table" else psm), fast=fast)
        lines = []
        for b in r.blocks:
            for l in b.lines:
                for wd in l.words:
                    wx, wy, ww, wh = wd.bbox
                    wd.bbox = (wx + x, wy + y, ww, wh)
                lines.append(l)
        if a.kind == "table":
            lines = words_to_lines([wd for l in lines for wd in l.words])
            blk = Block(lines=lines, bbox=a.rect, kind="table")
            blk.rows = build_table(lines)
        else:
            blk = Block(lines=lines, bbox=a.rect, kind="text")
        result.blocks.append(blk)
    page.ocr = result
    page.text_override = None
    return result


def apply_text_edit(page: ProjectPage, new_text: str):
    """Map edited text-pane content back onto the OCR blocks (keeps geometry when possible)."""
    if not page.ocr:
        page.text_override = new_text
        return
    textual = [b for b in page.ocr.blocks if b.kind != "picture"]
    paras = [p for p in new_text.split("\n\n")]
    paras = [p for p in paras if p.strip()]
    if len(paras) != len(textual):
        page.text_override = new_text
        return
    for blk, para in zip(textual, paras):
        if blk.text == para:
            continue
        new_lines = para.split("\n")
        if blk.kind == "table":
            blk.rows = [r.split("\t") for r in new_lines]
        old = blk.lines
        rebuilt = []
        for i, lt in enumerate(new_lines):
            ref = old[i].bbox if i < len(old) else (old[-1].bbox if old else blk.bbox)
            toks = lt.split()
            if not toks:
                continue
            total = sum(len(t) + 1 for t in toks)
            x = ref[0]
            ws = []
            for t in toks:
                wwid = int(ref[2] * (len(t) + 1) / max(1, total))
                ws.append(Word(t, (x, ref[1], max(1, wwid - 2), ref[3]), 100.0))
                x += wwid
            rebuilt.append(Line(ws))
        blk.lines = rebuilt
    page.text_override = None
