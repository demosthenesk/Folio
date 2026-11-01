"""High-level conversion jobs shared by the start window, PDF editor, hot folder and Snap."""
from pathlib import Path

import pymupdf
from PIL import Image

from . import imageproc, ocr
from .document import LoadedPage, as_pdf_path, kind_of, load_pages
from .exporters import ExportPage, export
from .settings import settings


class Cancelled(Exception):
    pass


def _check(cancel):
    if cancel and cancel():
        raise Cancelled()


def page_needs_ocr(lp: LoadedPage, mode):
    if mode == "ocr" or lp.text_layer is None:
        return True
    if mode == "text":
        return False
    n = sum(1 for _ in lp.text_layer.words())
    return n < 5


def recognize_files(files, langs=None, pdf_mode=None, preprocess=None, progress=None, cancel=None, dpi=None):
    """Load and recognize every page of `files`; returns ExportPage list."""
    langs = langs or settings()["languages"]
    pdf_mode = pdf_mode or settings()["pdf_mode"]
    pre = settings()["preprocess"] if preprocess is None else preprocess
    dpi = dpi or settings()["ocr_dpi"]
    out = []
    total = 0
    for f in files:
        from .document import page_count
        total += max(1, page_count(f))
    done = 0
    for f in files:
        for lp in load_pages(f, dpi=dpi):
            _check(cancel)
            if progress:
                progress(done, total, f"Recognizing page {done + 1} of {total}...")
            if page_needs_ocr(lp, pdf_mode):
                imgs = imageproc.preprocess(lp.image, pre) if kind_of(f) == "image" or lp.text_layer is None else [lp.image]
                for im in imgs:
                    _check(cancel)
                    out.append(ExportPage(im, ocr.recognize(im, langs), name=Path(f).name))
            else:
                out.append(ExportPage(lp.image, lp.text_layer, name=Path(f).name))
            done += 1
    if progress:
        progress(total, total, "Done")
    return out


def add_ocr_layer(doc: "pymupdf.Document", pno: int, langs, dpi=300):
    """Recognize one PDF page and overlay an invisible text layer (keeps the original page content)."""
    page = doc[pno]
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img.info["dpi"] = (dpi, dpi)
    data = ocr.text_only_pdf(img, langs)
    tdoc = pymupdf.open("pdf", data)
    rot = page.rotation
    page.set_rotation(0)
    page.show_pdf_page(page.rect, tdoc, 0, overlay=True)
    page.set_rotation(rot)


def make_pdf(files, out, langs=None, recognize=True, pdf_mode=None, progress=None, cancel=None):
    """Combine `files` into one PDF; image pages get a text layer, existing PDF pages are kept as-is."""
    langs = langs or settings()["languages"]
    pdf_mode = pdf_mode or settings()["pdf_mode"]
    res = pymupdf.open()
    pending = []
    for f in files:
        _check(cancel)
        k = kind_of(f)
        if k == "image":
            with Image.open(f) as im:
                n = getattr(im, "n_frames", 1)
            for lp in load_pages(f):
                img = lp.image
                dpi = float(img.info.get("dpi", (300, 300))[0] or 300)
                pg = res.new_page(width=img.width * 72 / dpi, height=img.height * 72 / dpi)
                from .exporters import _img_bytes
                data, _ = _img_bytes(img)
                pg.insert_image(pg.rect, stream=data)
                pending.append(len(res) - 1)
        else:
            src = pymupdf.open(as_pdf_path(f))
            start = len(res)
            res.insert_pdf(src)
            for i in range(start, len(res)):
                has = len(res[i].get_text("words")) >= 5
                if pdf_mode == "ocr" or not has:
                    pending.append(i)
    if recognize and ocr.available():
        for n, pno in enumerate(pending):
            _check(cancel)
            if progress:
                progress(n, len(pending), f"Recognizing page {n + 1} of {len(pending)}...")
            try:
                add_ocr_layer(res, pno, langs)
            except ocr.OcrError:
                raise
    res.set_metadata({**(res.metadata or {}), "producer": "Folio OCR"})
    res.save(out, garbage=3, deflate=True)
    return [out]


def convert(files, fmt, out, langs=None, progress=None, cancel=None):
    if fmt == "pdf":
        return make_pdf(files, out, langs, True, progress=progress, cancel=cancel)
    pages = recognize_files(files, langs, progress=progress, cancel=cancel)
    return export(pages, fmt, out)
