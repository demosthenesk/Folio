"""OCR engine wrapper around the Tesseract command-line program.

Results are returned as a small layout tree (page -> blocks -> lines -> words) with pixel
bounding boxes and confidences, which every Folio app uses for display, verification and export.
"""
import io
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from PIL import Image

from . import paths
from .settings import settings

LANG_NAMES = {
    "eng": "English", "ell": "Greek", "grc": "Greek (Ancient)", "deu": "German", "fra": "French",
    "spa": "Spanish", "ita": "Italian", "por": "Portuguese", "nld": "Dutch", "pol": "Polish",
    "rus": "Russian", "ukr": "Ukrainian", "tur": "Turkish", "ara": "Arabic", "heb": "Hebrew",
    "hin": "Hindi", "ind": "Indonesian", "jpn": "Japanese", "jpn_vert": "Japanese (vertical)",
    "kor": "Korean", "chi_sim": "Chinese (Simplified)", "chi_tra": "Chinese (Traditional)",
    "tha": "Thai", "vie": "Vietnamese", "ces": "Czech", "swe": "Swedish", "dan": "Danish",
    "fin": "Finnish", "nor": "Norwegian", "hun": "Hungarian", "ron": "Romanian", "bul": "Bulgarian",
    "srp": "Serbian", "hrv": "Croatian", "slk": "Slovak", "slv": "Slovenian", "lat": "Latin",
    "cat": "Catalan", "est": "Estonian", "lav": "Latvian", "lit": "Lithuanian", "equ": "Math / equations",
}


def lang_name(code):
    return LANG_NAMES.get(code, code)


class OcrError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    bbox: tuple  # x, y, w, h in image pixels
    conf: float = 100.0

    @property
    def x2(self):
        return self.bbox[0] + self.bbox[2]


@dataclass
class Line:
    words: List[Word] = field(default_factory=list)

    @property
    def text(self):
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self):
        return union([w.bbox for w in self.words])


@dataclass
class Block:
    lines: List[Line] = field(default_factory=list)
    bbox: tuple = (0, 0, 0, 0)
    kind: str = "text"  # text | table | picture | barcode
    rows: Optional[list] = None  # for tables: list of list of cell strings

    @property
    def text(self):
        if self.kind == "table" and self.rows:
            return "\n".join("\t".join(r) for r in self.rows)
        return "\n".join(l.text for l in self.lines)

    def words(self):
        for l in self.lines:
            yield from l.words


@dataclass
class OcrPage:
    width: int
    height: int
    blocks: List[Block] = field(default_factory=list)
    source: str = "ocr"  # ocr | pdftext

    def words(self):
        for b in self.blocks:
            yield from b.words()

    def text(self, keep_line_breaks=True):
        out = []
        for b in self.blocks:
            if b.kind == "picture":
                continue
            if b.kind == "table" or keep_line_breaks:
                out.append(b.text)
            else:
                out.append(join_paragraph([l.text for l in b.lines]))
        return "\n\n".join(t for t in out if t.strip())

    def to_dict(self):
        return {"w": self.width, "h": self.height, "src": self.source, "blocks": [
            {"bbox": list(b.bbox), "kind": b.kind, "rows": b.rows,
             "lines": [[[w.text, list(w.bbox), w.conf] for w in l.words] for l in b.lines]}
            for b in self.blocks]}

    @classmethod
    def from_dict(cls, d):
        p = cls(d["w"], d["h"], source=d.get("src", "ocr"))
        for b in d["blocks"]:
            blk = Block(bbox=tuple(b["bbox"]), kind=b["kind"], rows=b.get("rows"))
            for l in b["lines"]:
                blk.lines.append(Line([Word(t, tuple(bb), c) for t, bb, c in l]))
            p.blocks.append(blk)
        return p


def union(boxes):
    boxes = [b for b in boxes if b and b[2] > 0 and b[3] > 0]
    if not boxes:
        return (0, 0, 0, 0)
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return (x1, y1, x2 - x1, y2 - y1)


def join_paragraph(lines):
    out = ""
    for l in lines:
        l = l.strip()
        if not l:
            continue
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + l
        else:
            out = (out + " " + l) if out else l
    return out


# ---------------------------------------------------------------- engine
def available():
    return bool(paths.tesseract_bin())


def available_languages():
    tb = paths.tesseract_bin()
    if not tb:
        return []
    td = paths.merged_tessdata()
    langs = sorted(f.stem for f in td.glob("*.traineddata") if f.stem not in ("osd",))
    return langs


def lang_arg(langs):
    avail = set(available_languages())
    use = [l for l in (langs or []) if l in avail] or (["eng"] if "eng" in avail else sorted(avail)[:1])
    if not use:
        raise OcrError("No OCR languages are installed. Put *.traineddata files into "
                       f"{paths.user_tessdata_dir()}")
    return "+".join(use)


def _env():
    env = dict(os.environ)
    env["TESSDATA_PREFIX"] = str(paths.merged_tessdata())
    env.setdefault("OMP_THREAD_LIMIT", "2")
    return env


def _run(args, stdin_bytes=None, timeout=600):
    tb = paths.tesseract_bin()
    if not tb:
        raise OcrError("Tesseract OCR engine not found.")
    # input/output first, then --tessdata-dir (must precede -l), then the remaining options
    cmd =[tb, args[0], args[1], "--tessdata-dir", str(paths.merged_tessdata())] + args[2:]
    p = subprocess.run(cmd, input=stdin_bytes, capture_output=True, env=_env(), timeout=timeout)
    if p.returncode != 0:
        raise OcrError(p.stderr.decode("utf-8", "replace").strip() or "Tesseract failed")
    return p.stdout


def _png_bytes(img: Image.Image):
    buf = io.BytesIO()
    if img.mode not in ("RGB", "L", "1"):
        img = img.convert("RGB")
    img.save(buf, "PNG", dpi=img.info.get("dpi", (300, 300)))
    return buf.getvalue()


def _psm(mode):
    return {"auto": "3", "column": "4", "block": "6", "line": "7", "word": "8", "sparse": "11"}.get(mode, "3")


def recognize(img: Image.Image, langs=None, psm="auto", fast=None) -> OcrPage:
    """OCR an image and return the layout tree."""
    if fast is None:
        fast = settings()["recognition_mode"] == "fast"
    dpi = int(img.info.get("dpi", (300, 300))[0] or 300)
    args = ["stdin", "stdout", "-l", lang_arg(langs or settings()["languages"]), "--psm", _psm(psm),
            "--dpi", str(max(70, dpi)), "-c", "preserve_interword_spaces=1"]
    if fast:
        args += ["--oem", "1"]
    args.append("tsv")
    out = _run(args, _png_bytes(img)).decode("utf-8", "replace")
    return parse_tsv(out, img.width, img.height)


def parse_tsv(tsv, w, h) -> OcrPage:
    page = OcrPage(w, h)
    blocks = {}
    lines = {}
    order = []
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12:
            continue
        level = int(c[0])
        key_b = (c[2],)
        key_l = (c[2], c[3], c[4])
        bbox = (int(c[6]), int(c[7]), int(c[8]), int(c[9]))
        if level == 2:
            blk = Block(bbox=bbox)
            blocks[key_b] = blk
            page.blocks.append(blk)
        elif level == 4:
            ln = Line()
            lines[key_l] = ln
            if key_b in blocks:
                blocks[key_b].lines.append(ln)
        elif level == 5:
            txt = c[11].strip()
            if not txt:
                continue
            try:
                conf = float(c[10])
            except ValueError:
                conf = 0.0
            if key_l in lines:
                lines[key_l].words.append(Word(txt, bbox, conf))
    for b in page.blocks:
        b.lines = [l for l in b.lines if l.words]
        if not b.lines and b.bbox[2] * b.bbox[3] > 0.01 * w * h:
            b.kind = "picture"
    page.blocks = [b for b in page.blocks if b.lines or b.kind == "picture"]
    return page


def text_only_pdf(img: Image.Image, langs=None, fast=None) -> bytes:
    """Return a one-page PDF containing only the invisible text layer for `img` (Tesseract renderer)."""
    if fast is None:
        fast = settings()["recognition_mode"] == "fast"
    dpi = int(img.info.get("dpi", (300, 300))[0] or 300)
    args = ["stdin", "stdout", "-l", lang_arg(langs or settings()["languages"]), "--dpi", str(dpi),
            "-c", "textonly_pdf=1"]
    if fast:
        args += ["--oem", "1"]
    args.append("pdf")
    return _run(args, _png_bytes(img))


def detect_orientation(img: Image.Image):
    """Return clockwise rotation (0/90/180/270) needed to make text upright, or 0 if unknown."""
    try:
        small = img.copy()
        small.thumbnail((2000, 2000))
        out = _run(["stdin", "stdout", "--psm", "0"], _png_bytes(small), timeout=60).decode()
        m = re.search(r"Rotate:\s+(\d+)", out)
        conf = re.search(r"Orientation confidence:\s+([\d.]+)", out)
        if m and (not conf or float(conf.group(1)) > 1.5):
            return int(m.group(1))
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------- tables
def build_table(lines: List[Line], min_gap=None):
    """Reconstruct a table grid from OCR lines inside a table area (column clustering by gaps)."""
    rows_words = [sorted(l.words, key=lambda w: w.bbox[0]) for l in lines if l.words]
    if not rows_words:
        return []
    heights = sorted(w.bbox[3] for r in rows_words for w in r)
    hmed = heights[len(heights) // 2] if heights else 12
    gap = min_gap or max(12, int(hmed * 1.2))
    # split each line into cells by large horizontal gaps
    rows = []
    for r in rows_words:
        cells, cur = [], [r[0]]
        for a, b in zip(r, r[1:]):
            if b.bbox[0] - a.x2 > gap:
                cells.append(cur)
                cur = [b]
            else:
                cur.append(b)
        cells.append(cur)
        rows.append([(c[0].bbox[0], c[-1].x2, " ".join(w.text for w in c)) for c in cells])
    # cluster column x-positions across rows
    starts = sorted(c[0] for r in rows for c in r)
    cols = []
    for s in starts:
        if not cols or s - cols[-1][-1] > gap * 1.5:
            cols.append([s])
        else:
            cols[-1].append(s)
    centers = [sum(c) / len(c) for c in cols]
    grid = []
    for r in rows:
        line = [""] * len(centers)
        for x1, x2, t in r:
            i = min(range(len(centers)), key=lambda k: abs(centers[k] - x1))
            line[i] = (line[i] + " " + t).strip()
        grid.append(line)
    return grid


def words_to_lines(words: List[Word]) -> List[Line]:
    """Group loose words (e.g. from a PDF text layer) into lines."""
    ws = sorted(words, key=lambda w: (w.bbox[1] + w.bbox[3] / 2, w.bbox[0]))
    lines: List[Line] = []
    for w in ws:
        cy = w.bbox[1] + w.bbox[3] / 2
        if lines:
            lb = lines[-1].bbox
            if abs((lb[1] + lb[3] / 2) - cy) < max(lb[3], w.bbox[3]) * 0.5:
                lines[-1].words.append(w)
                continue
        lines.append(Line([w]))
    for l in lines:
        l.words.sort(key=lambda w: w.bbox[0])
    return lines
