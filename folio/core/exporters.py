"""Save recognized pages to the formats offered by 'Save As' / 'Convert to' / 'Send to'."""
import base64
import html
import io
import os
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pymupdf
from PIL import Image

from . import paths
from .document import unicode_font_file
from .ocr import Block, OcrPage, build_table, join_paragraph
from .settings import settings

FORMATS = {
    # key: (menu label, extension, file-dialog filter)
    "pdf": ("Searchable PDF Document", ".pdf", "PDF Document (*.pdf)"),
    "pdf_image": ("Image-Only PDF Document", ".pdf", "PDF Document (*.pdf)"),
    "docx": ("Microsoft Word Document", ".docx", "Word Document (*.docx)"),
    "xlsx": ("Microsoft Excel Workbook", ".xlsx", "Excel Workbook (*.xlsx)"),
    "pptx": ("Microsoft PowerPoint Presentation", ".pptx", "PowerPoint Presentation (*.pptx)"),
    "odt": ("ODT Document", ".odt", "OpenDocument Text (*.odt)"),
    "html": ("HTML Document", ".html", "HTML Document (*.html *.htm)"),
    "epub": ("EPUB Document", ".epub", "EPUB Document (*.epub)"),
    "fb2": ("FB2 Document", ".fb2", "FictionBook 2 (*.fb2)"),
    "djvu": ("DjVu Document", ".djvu", "DjVu Document (*.djvu)"),
    "rtf": ("RTF Document", ".rtf", "Rich Text Format (*.rtf)"),
    "txt": ("TXT Document", ".txt", "Text (*.txt)"),
    "csv": ("CSV Document", ".csv", "CSV Document (*.csv)"),
}
IMAGE_FORMATS = {"png": "PNG Image (*.png)", "jpg": "JPEG Image (*.jpg)", "tif": "TIFF Image (*.tif)",
                 "bmp": "BMP Image (*.bmp)"}


class ExportError(RuntimeError):
    pass


@dataclass
class ExportPage:
    image: Image.Image
    ocr: Optional[OcrPage] = None
    text_override: Optional[str] = None
    name: str = ""


def _dpi(img):
    return float(img.info.get("dpi", (300, 300))[0] or 300)


def page_items(p: ExportPage, keep_line_breaks=None, with_pictures=None):
    """Logical content of a page: [('para', text) | ('table', rows) | ('picture', PIL image, bbox)]."""
    s = settings()
    klb = s["keep_line_breaks"] if keep_line_breaks is None else keep_line_breaks
    pics = s["export_images"] if with_pictures is None else with_pictures
    items = []
    if p.text_override is not None:
        for para in p.text_override.split("\n\n"):
            if para.strip():
                items.append(("para", para if klb else join_paragraph(para.split("\n"))))
        return items
    if not p.ocr:
        return items
    for b in p.ocr.blocks:
        if b.kind == "picture":
            if pics and b.bbox[2] > 20 and b.bbox[3] > 20:
                x, y, w, h = b.bbox
                items.append(("picture", p.image.crop((x, y, x + w, y + h)), b.bbox))
        elif b.kind == "table":
            rows = b.rows or build_table(b.lines)
            if rows:
                items.append(("table", rows))
        else:
            if klb:
                t = "\n".join(l.text for l in b.lines)
            else:
                t = join_paragraph([l.text for l in b.lines])
            if t.strip():
                items.append(("para", t))
    return items


def plain_text(pages: List[ExportPage], page_sep="\n\n\f"):
    parts = []
    for p in pages:
        out = []
        for it in page_items(p, with_pictures=False):
            if it[0] == "para":
                out.append(it[1])
            elif it[0] == "table":
                out.append("\n".join("\t".join(r) for r in it[1]))
        parts.append("\n\n".join(out))
    return page_sep.join(parts)


def table_text(pages: List[ExportPage]):
    rows = []
    for p in pages:
        rows.extend(sheet_rows(p))
    return "\n".join("\t".join(r) for r in rows)


def sheet_rows(p: ExportPage):
    rows = []
    for it in page_items(p, with_pictures=False):
        if it[0] == "table":
            rows.extend(it[1])
        else:
            for line in it[1].split("\n"):
                rows.append(line.split("\t"))
        rows.append([])
    while rows and not rows[-1]:
        rows.pop()
    return rows


def sheet_rows_from_ocr(p: ExportPage):
    """Spreadsheet rows keeping column structure of every text block (Excel export)."""
    if p.text_override is not None or not p.ocr:
        return sheet_rows(p)
    rows = []
    for b in p.ocr.blocks:
        if b.kind == "picture":
            continue
        grid = b.rows if (b.kind == "table" and b.rows) else build_table(b.lines)
        rows.extend(grid)
        rows.append([])
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _img_bytes(img: Image.Image, quality=None):
    quality = quality or settings()["pdf_image_quality"]
    buf = io.BytesIO()
    if img.mode == "1" or (img.mode == "L" and quality == "compact"):
        img.convert("L").save(buf, "PNG", optimize=True)
        return buf.getvalue(), "png"
    q = {"high": 92, "balanced": 80, "compact": 60}.get(quality, 80)
    src = img
    if quality == "compact" and _dpi(img) > 200:
        f = 200 / _dpi(img)
        src = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
    src.convert("RGB" if src.mode != "L" else "L").save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue(), "jpg"


def _font_for(text):
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:
            return "japan"
        if 0xAC00 <= o <= 0xD7AF:
            return "korea"
        if 0x4E00 <= o <= 0x9FFF:
            return "china-s"
    return None


def add_text_layer(page: "pymupdf.Page", p: ExportPage, scale: float, visible=False):
    """Write an invisible (render mode 3) text layer from OCR word boxes onto a PDF page."""
    font_file = unicode_font_file()
    if font_file:
        page.insert_font(fontname="FolioF", fontfile=font_file)
        font = pymupdf.Font(fontfile=font_file)
        fname = "FolioF"
    else:
        font, fname = pymupdf.Font("helv"), "helv"
    rm = 0 if visible else 3

    def put(text, bbox):
        x, y, w, h = [v * scale for v in bbox]
        if w <= 0 or h <= 0 or not text.strip():
            return
        cjk = _font_for(text)
        fs = max(1.0, h * 0.85)
        fn, f = (cjk, pymupdf.Font(cjk)) if cjk else (fname, font)
        tl = f.text_length(text, fs) or 1
        sx = max(0.05, min(20, w / tl))
        pt = pymupdf.Point(x, y + h * 0.82)
        try:
            page.insert_text(pt, text, fontsize=fs, fontname=fn, render_mode=rm,
                             morph=(pt, pymupdf.Matrix(sx, 1)))
        except Exception:
            pass

    if p.text_override is not None or not p.ocr:
        if p.text_override:
            # no geometry: place lines top-down over the page area
            lines = [l for l in p.text_override.split("\n")]
            h = page.rect.height / max(40, len(lines) + 2)
            for i, l in enumerate(lines):
                put(l, (36 / scale, (36 + i * h) / scale, (page.rect.width - 72) / scale, h * 0.9 / scale))
        return
    for b in p.ocr.blocks:
        for l in b.lines:
            for w in l.words:
                put(w.text, w.bbox)


def export_pdf(pages: List[ExportPage], out, searchable=True, quality=None):
    doc = pymupdf.open()
    for p in pages:
        dpi = _dpi(p.image)
        scale = 72.0 / dpi
        pg = doc.new_page(width=p.image.width * scale, height=p.image.height * scale)
        data, _ = _img_bytes(p.image, quality)
        pg.insert_image(pg.rect, stream=data)
        if searchable:
            add_text_layer(pg, p, scale)
    doc.set_metadata({"producer": "Folio OCR", "creator": "Folio"})
    try:
        doc.subset_fonts()
    except Exception:
        pass
    doc.save(out, garbage=3, deflate=True)
    return [out]


def export_txt(pages, out):
    sep = "\n\n\f" if settings()["keep_page_breaks"] else "\n\n"
    Path(out).write_text(plain_text(pages, sep), "utf-8")
    return [out]


def export_csv(pages, out):
    import csv
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for i, p in enumerate(pages):
            if i:
                w.writerow([])
            for r in sheet_rows_from_ocr(p):
                w.writerow(r)
    return [out]


def export_xlsx(pages, out):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    wb.remove(wb.active)
    for i, p in enumerate(pages):
        ws = wb.create_sheet(f"Page {i + 1}")
        for r in sheet_rows_from_ocr(p):
            row = []
            for v in r:
                vv = v.strip()
                try:
                    if vv and vv.replace(",", "").replace(".", "", 1).lstrip("-").isdigit():
                        row.append(float(vv.replace(",", "")) if "." in vv else int(vv.replace(",", "")))
                        continue
                except ValueError:
                    pass
                row.append(vv)
            ws.append(row)
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(60, max(8, width + 2))
    if not wb.sheetnames:
        wb.create_sheet("Page 1")
    wb.save(out)
    return [out]


def export_docx(pages, out):
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Inches, Pt
    d = Document()
    st = d.styles["Normal"]
    st.font.name = "DejaVu Sans"
    st.font.size = Pt(11)
    for i, p in enumerate(pages):
        for it in page_items(p):
            if it[0] == "para":
                d.add_paragraph(it[1])
            elif it[0] == "table":
                rows = it[1]
                ncol = max(len(r) for r in rows)
                t = d.add_table(rows=len(rows), cols=ncol)
                t.style = "Table Grid"
                for ri, r in enumerate(rows):
                    for ci, v in enumerate(r):
                        t.cell(ri, ci).text = v
                d.add_paragraph("")
            elif it[0] == "picture":
                buf = io.BytesIO()
                it[1].convert("RGB").save(buf, "PNG")
                buf.seek(0)
                width = min(6.3, it[1].width / _dpi(p.image))
                d.add_picture(buf, width=Inches(max(0.3, width)))
        if i < len(pages) - 1 and settings()["keep_page_breaks"]:
            d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    d.save(out)
    return [out]


def export_pptx(pages, out):
    from pptx import Presentation
    from pptx.util import Emu, Pt
    prs = Presentation()
    blank = prs.slide_layouts[6]
    sw, sh = prs.slide_width, prs.slide_height
    for p in pages:
        s = prs.slides.add_slide(blank)
        W, H = p.image.width, p.image.height
        f = min(sw / W, sh / H)
        ox, oy = (sw - W * f) / 2, (sh - H * f) / 2
        if not p.ocr or p.text_override is not None:
            tb = s.shapes.add_textbox(Emu(int(sw * 0.05)), Emu(int(sh * 0.05)), Emu(int(sw * 0.9)), Emu(int(sh * 0.9)))
            tb.text_frame.word_wrap = True
            tb.text_frame.text = p.text_override or ""
            continue
        for b in p.ocr.blocks:
            x, y, w, h = b.bbox
            box = (Emu(int(ox + x * f)), Emu(int(oy + y * f)), Emu(max(1, int(w * f))), Emu(max(1, int(h * f))))
            if b.kind == "picture":
                buf = io.BytesIO()
                p.image.crop((x, y, x + w, y + h)).convert("RGB").save(buf, "PNG")
                buf.seek(0)
                s.shapes.add_picture(buf, *box)
                continue
            tb = s.shapes.add_textbox(*box)
            tf = tb.text_frame
            tf.word_wrap = True
            lh = sorted(l.bbox[3] for l in b.lines)[len(b.lines) // 2] if b.lines else 12
            size = max(6, min(60, lh * f / 12700 * 0.75))
            first = True
            for l in (b.rows and ["\t".join(r) for r in b.rows]) or [l.text for l in b.lines]:
                para = tf.paragraphs[0] if first else tf.add_paragraph()
                first = False
                para.text = l
                for r in para.runs:
                    r.font.size = Pt(size)
    prs.save(out)
    return [out]


def _html_body(pages, embed_images=True):
    parts = []
    for i, p in enumerate(pages):
        parts.append(f'<div class="page" id="page{i + 1}">')
        for it in page_items(p):
            if it[0] == "para":
                parts.append("<p>" + html.escape(it[1]).replace("\n", "<br/>") + "</p>")
            elif it[0] == "table":
                parts.append("<table>" + "".join(
                    "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in it[1]) + "</table>")
            elif it[0] == "picture" and embed_images:
                buf = io.BytesIO()
                it[1].convert("RGB").save(buf, "JPEG", quality=85)
                parts.append(f'<p><img alt="" src="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"/></p>')
        parts.append("</div>")
    return "\n".join(parts)


CSS = ("body{font-family:'DejaVu Sans',sans-serif;max-width:52em;margin:2em auto;line-height:1.45}"
       "table{border-collapse:collapse;margin:1em 0}td{border:1px solid #999;padding:3px 6px}"
       ".page{margin-bottom:2em}img{max-width:100%}")


def export_html(pages, out, title="Document"):
    Path(out).write_text(
        f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
        f"<style>{CSS}</style></head><body>\n{_html_body(pages)}\n</body></html>", "utf-8")
    return [out]


def export_epub(pages, out, title="Document"):
    uid = "urn:uuid:" + str(uuid.uuid4())
    chapters, images = [], []
    for i, p in enumerate(pages):
        body = []
        for it in page_items(p):
            if it[0] == "para":
                body.append("<p>" + html.escape(it[1]).replace("\n", "<br/>") + "</p>")
            elif it[0] == "table":
                body.append("<table>" + "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>"
                                                for r in it[1]) + "</table>")
            elif it[0] == "picture":
                name = f"img{len(images) + 1}.jpg"
                buf = io.BytesIO()
                it[1].convert("RGB").save(buf, "JPEG", quality=85)
                images.append((name, buf.getvalue()))
                body.append(f'<p><img alt="" src="{name}"/></p>')
        chapters.append(f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
                        f'<head><title>Page {i + 1}</title><link rel="stylesheet" href="style.css"/></head>'
                        f'<body>{"".join(body)}</body></html>')
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        man = ['<item id="css" href="style.css" media-type="text/css"/>',
               '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
        spine = []
        for i, c in enumerate(chapters):
            z.writestr(f"OEBPS/p{i + 1}.xhtml", c, compress_type=zipfile.ZIP_DEFLATED)
            man.append(f'<item id="p{i + 1}" href="p{i + 1}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="p{i + 1}"/>')
        for n, data in images:
            z.writestr(f"OEBPS/{n}", data)
            man.append(f'<item id="{n.split(".")[0]}" href="{n}" media-type="image/jpeg"/>')
        z.writestr("OEBPS/style.css", CSS)
        nav = "".join(f'<li><a href="p{i + 1}.xhtml">Page {i + 1}</a></li>' for i in range(len(chapters)))
        z.writestr("OEBPS/nav.xhtml", '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
                   f'<nav epub:type="toc"><ol>{nav}</ol></nav></body></html>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="uid">{uid}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
                   f'<dc:language>en</dc:language><meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
                   f'</metadata><manifest>{"".join(man)}</manifest><spine>{"".join(spine)}</spine></package>')
    return [out]


def export_fb2(pages, out, title="Document"):
    bins, sections = [], []
    for i, p in enumerate(pages):
        body = []
        for it in page_items(p):
            if it[0] == "para":
                body.append("<p>" + html.escape(it[1]).replace("\n", " ") + "</p>")
            elif it[0] == "table":
                body.append("<table>" + "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>"
                                                for r in it[1]) + "</table>")
            elif it[0] == "picture":
                bid = f"img{len(bins) + 1}.jpg"
                buf = io.BytesIO()
                it[1].convert("RGB").save(buf, "JPEG", quality=85)
                bins.append(f'<binary id="{bid}" content-type="image/jpeg">{base64.b64encode(buf.getvalue()).decode()}</binary>')
                body.append(f'<image l:href="#{bid}"/>')
        sections.append(f"<section><title><p>Page {i + 1}</p></title>{''.join(body) or '<empty-line/>'}</section>")
    Path(out).write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><genre>reference</genre>'
        f'<book-title>{html.escape(title)}</book-title><lang>en</lang></title-info><document-info>'
        '<program-used>Folio</program-used></document-info></description>'
        f'<body>{"".join(sections)}</body>{"".join(bins)}</FictionBook>', "utf-8")
    return [out]


def _rtf_escape(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\t":
            out.append("\\tab ")
        elif o > 127:
            out.append(f"\\u{o if o < 32768 else o - 65536}?")
        else:
            out.append(ch)
    return "".join(out)


def export_rtf(pages, out):
    parts = ["{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0 DejaVu Sans;}}\\fs22\n"]
    for i, p in enumerate(pages):
        for it in page_items(p, with_pictures=False):
            if it[0] == "para":
                parts.append("{\\pard " + _rtf_escape(it[1]) + "\\par}\n")
            elif it[0] == "table":
                for r in it[1]:
                    n = max(1, len(r))
                    parts.append("\\trowd" + "".join(f"\\clbrdrt\\brdrs\\clbrdrb\\brdrs\\clbrdrl\\brdrs\\clbrdrr\\brdrs\\cellx{(k + 1) * 9000 // n}"
                                                     for k in range(n)))
                    parts.append("".join("\\pard\\intbl " + _rtf_escape(c) + "\\cell " for c in r) + "\\row\n")
                parts.append("\\pard\\par\n")
        if i < len(pages) - 1 and settings()["keep_page_breaks"]:
            parts.append("\\page\n")
    parts.append("}")
    Path(out).write_text("".join(parts), "ascii", errors="replace")
    return [out]


def export_odt(pages, out):
    from odf.draw import Frame, Image as OImage
    from odf.opendocument import OpenDocumentText
    from odf.style import ParagraphProperties, Style, TableCellProperties
    from odf.table import Table, TableCell, TableColumn, TableRow
    from odf.text import P
    d = OpenDocumentText()
    cell_style = Style(name="Cell", family="table-cell")
    cell_style.addElement(TableCellProperties(border="0.5pt solid #000000", padding="0.05cm"))
    d.automaticstyles.addElement(cell_style)
    pb = Style(name="PB", family="paragraph")
    pb.addElement(ParagraphProperties(breakbefore="page"))
    d.automaticstyles.addElement(pb)
    first_on_page = False
    for i, p in enumerate(pages):
        first_on_page = i > 0 and settings()["keep_page_breaks"]
        for it in page_items(p):
            if it[0] == "para":
                for k, line in enumerate(it[1].split("\n")):
                    d.text.addElement(P(text=line, stylename=pb if first_on_page else None) if first_on_page else P(text=line))
                    first_on_page = False
            elif it[0] == "table":
                rows = it[1]
                ncol = max(len(r) for r in rows)
                t = Table()
                t.addElement(TableColumn(numbercolumnsrepeated=ncol))
                for r in rows:
                    tr = TableRow()
                    for k in range(ncol):
                        c = TableCell(stylename=cell_style)
                        c.addElement(P(text=r[k] if k < len(r) else ""))
                        tr.addElement(c)
                    t.addElement(tr)
                d.text.addElement(t)
            elif it[0] == "picture":
                buf = io.BytesIO()
                it[1].convert("RGB").save(buf, "PNG")
                href = d.addPictureFromString(buf.getvalue(), "image/png")
                dpi = _dpi(p.image)
                wcm = min(17.0, it[1].width / dpi * 2.54)
                hcm = wcm * it[1].height / max(1, it[1].width)
                fr = Frame(width=f"{wcm:.2f}cm", height=f"{hcm:.2f}cm", anchortype="as-char")
                fr.addElement(OImage(href=href))
                para = P()
                para.addElement(fr)
                d.text.addElement(para)
    d.save(out)
    return [out]


def export_djvu(pages, out):
    c44 = paths.which_external("c44")
    djvm = paths.which_external("djvm")
    if not (c44 and djvm):
        raise ExportError("Saving to DjVu requires djvulibre-bin (sudo apt install djvulibre-bin).")
    tmp = Path(tempfile.mkdtemp(prefix="folio-djvu-"))
    parts = []
    for i, p in enumerate(pages):
        src = tmp / f"p{i}.ppm"
        p.image.convert("RGB").save(src)
        dj = tmp / f"p{i}.djvu"
        paths.run_external([c44, "-dpi", str(int(_dpi(p.image))), str(src), str(dj)], wait=True, capture_output=True)
        parts.append(str(dj))
    paths.run_external([djvm, "-c", str(out)] + parts, wait=True, capture_output=True)
    if not Path(out).exists():
        raise ExportError("DjVu encoding failed.")
    return [out]


def export_images(pages, out, fmt="png"):
    """Save page images. Multi-page TIFF when fmt == 'tif', otherwise out_1.png, out_2.png..."""
    out = Path(out)
    if fmt == "tif":
        imgs = [p.image.convert("RGB") if p.image.mode not in ("L", "RGB") else p.image for p in pages]
        imgs[0].save(out, "TIFF", save_all=True, append_images=imgs[1:], compression="tiff_deflate",
                     dpi=pages[0].image.info.get("dpi", (300, 300)))
        return [str(out)]
    files = []
    for i, p in enumerate(pages):
        f = out if len(pages) == 1 else out.with_name(f"{out.stem}_{i + 1:03d}{out.suffix}")
        im = p.image
        kw = {"dpi": im.info.get("dpi", (300, 300))}
        if fmt == "jpg":
            im = im.convert("RGB")
            kw["quality"] = 90
        im.save(f, {"png": "PNG", "jpg": "JPEG", "bmp": "BMP"}[fmt], **kw)
        files.append(str(f))
    return files


def export(pages: List[ExportPage], fmt: str, out: str, title=None):
    if not pages:
        raise ExportError("Nothing to save.")
    title = title or Path(out).stem
    if fmt == "pdf":
        return export_pdf(pages, out, True)
    if fmt == "pdf_image":
        return export_pdf(pages, out, False)
    fn = {"docx": export_docx, "xlsx": export_xlsx, "pptx": export_pptx, "odt": export_odt, "rtf": export_rtf,
          "txt": export_txt, "csv": export_csv, "djvu": export_djvu}.get(fmt)
    if fn:
        return fn(pages, out)
    if fmt == "html":
        return export_html(pages, out, title)
    if fmt == "epub":
        return export_epub(pages, out, title)
    if fmt == "fb2":
        return export_fb2(pages, out, title)
    if fmt in IMAGE_FORMATS:
        return export_images(pages, out, fmt)
    raise ExportError(f"Unknown format {fmt}")
