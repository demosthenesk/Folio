"""Page-image processing: the operations of the OCR Editor's image editor and the preprocessing steps."""
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance


def _keep_dpi(src, dst):
    if "dpi" in src.info:
        dst.info["dpi"] = src.info["dpi"]
    return dst


def gray(img):
    return _keep_dpi(img, img.convert("L"))


def otsu_threshold(arr):
    hist = np.bincount(arr.ravel(), minlength=256).astype(float)
    total = arr.size
    sum_total = np.dot(np.arange(256), hist)
    sum_b, w_b, best, thr = 0.0, 0.0, 0.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def binarize(img):
    g = np.asarray(img.convert("L"))
    t = otsu_threshold(g)
    return _keep_dpi(img, Image.fromarray(((g > t) * 255).astype(np.uint8), "L"))


def estimate_skew(img, limit=6.0, step=0.2):
    """Projection-profile skew estimation; returns angle in degrees (counter-clockwise positive)."""
    small = img.convert("L")
    small.thumbnail((1200, 1200))
    a = np.asarray(small)
    ink = a < otsu_threshold(a)
    if ink.mean() < 0.002:
        return 0.0
    im = Image.fromarray((ink * 255).astype(np.uint8))
    best_a, best_s = 0.0, -1
    for ang in np.arange(-limit, limit + step / 2, step):
        r = np.asarray(im.rotate(ang, resample=Image.NEAREST, expand=False))
        prof = r.sum(axis=1).astype(float)
        s = np.sum(np.diff(prof) ** 2)
        if s > best_s:
            best_s, best_a = s, float(ang)
    return round(best_a, 2)


def deskew(img, angle=None):
    if angle is None:
        angle = estimate_skew(img)
    if abs(angle) < 0.1:
        return img
    fill = 255 if img.mode in ("L", "1") else (255, 255, 255)
    src = img if img.mode in ("L", "RGB") else img.convert("RGB")
    return _keep_dpi(img, src.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=fill))


def rotate(img, degrees_cw):
    if degrees_cw % 360 == 0:
        return img
    return _keep_dpi(img, img.rotate(-degrees_cw, expand=True))


def rotate_free(img, degrees_cw):
    fill = 255 if img.mode in ("L", "1") else (255, 255, 255)
    src = img if img.mode in ("L", "RGB") else img.convert("RGB")
    return _keep_dpi(img, src.rotate(-degrees_cw, resample=Image.BICUBIC, expand=True, fillcolor=fill))


def flip(img, horizontal=True):
    return _keep_dpi(img, ImageOps.mirror(img) if horizontal else ImageOps.flip(img))


def crop(img, rect):
    x, y, w, h = [int(v) for v in rect]
    return _keep_dpi(img, img.crop((max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h))))


def invert(img):
    src = img.convert("RGB") if img.mode not in ("L", "RGB") else img
    return _keep_dpi(img, ImageOps.invert(src))


def brightness_contrast(img, brightness=0, contrast=0):
    """brightness/contrast in -100..100"""
    out = img if img.mode in ("L", "RGB") else img.convert("RGB")
    if brightness:
        out = ImageEnhance.Brightness(out).enhance(1 + brightness / 100.0)
    if contrast:
        out = ImageEnhance.Contrast(out).enhance(1 + contrast / 100.0)
    return _keep_dpi(img, out)


def levels(img, black=0, white=255, gamma=1.0):
    src = img if img.mode in ("L", "RGB") else img.convert("RGB")
    lut = []
    for v in range(256):
        x = (v - black) / max(1, (white - black))
        x = min(1.0, max(0.0, x)) ** (1.0 / max(0.05, gamma))
        lut.append(int(round(x * 255)))
    return _keep_dpi(img, src.point(lut * (3 if src.mode == "RGB" else 1)))


def autocontrast(img):
    src = img if img.mode in ("L", "RGB") else img.convert("RGB")
    return _keep_dpi(img, ImageOps.autocontrast(src, cutoff=1))


def denoise(img):
    return _keep_dpi(img, img.filter(ImageFilter.MedianFilter(3)))


def photo_correction(img):
    """Whiten background and sharpen — for photos of documents."""
    src = img if img.mode in ("L", "RGB") else img.convert("RGB")
    g = np.asarray(src.convert("L")).astype(float)
    bg = np.asarray(Image.fromarray(g.astype(np.uint8)).filter(ImageFilter.GaussianBlur(25))).astype(float)
    norm = np.clip(g / np.maximum(bg, 1) * 255, 0, 255).astype(np.uint8)
    out = ImageOps.autocontrast(Image.fromarray(norm), cutoff=1).filter(ImageFilter.SHARPEN)
    return _keep_dpi(img, out)


def remove_color_marks(img, saturation=60):
    """Remove coloured stamps and pen marks (keeps dark/neutral ink)."""
    if img.mode != "RGB":
        return img
    hsv = np.asarray(img.convert("HSV")).copy()
    rgb = np.asarray(img).copy()
    mask = (hsv[..., 1] > saturation) & (hsv[..., 2] > 60)
    rgb[mask] = 255
    return _keep_dpi(img, Image.fromarray(rgb, "RGB"))


def erase(img, rect, color=None):
    out = img.copy()
    from PIL import ImageDraw
    x, y, w, h = [int(v) for v in rect]
    fill = color if color is not None else (255 if out.mode in ("L", "1") else (255, 255, 255))
    ImageDraw.Draw(out).rectangle([x, y, x + w, y + h], fill=fill)
    return _keep_dpi(img, out)


def set_resolution(img, dpi, resample=True):
    cur = int(img.info.get("dpi", (300, 300))[0] or 300)
    out = img
    if resample and cur != dpi:
        f = dpi / cur
        out = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))), Image.LANCZOS)
    out.info["dpi"] = (dpi, dpi)
    return out


def split_facing(img, vertical=True):
    """Split a two-page spread into two images (left/right or top/bottom)."""
    if vertical:
        a = np.asarray(img.convert("L"))
        w = a.shape[1]
        band = a[:, int(w * 0.4):int(w * 0.6)].mean(axis=0)
        cut = int(w * 0.4) + int(np.argmax(band))  # brightest column near the middle (gutter)
        return [crop(img, (0, 0, cut, img.height)), crop(img, (cut, 0, img.width - cut, img.height))]
    mid = img.height // 2
    return [crop(img, (0, 0, img.width, mid)), crop(img, (0, mid, img.width, img.height - mid))]


def looks_like_spread(img):
    return img.width > img.height * 1.25


def preprocess(img, opts):
    """Apply the 'image preprocessing' options before recognition; returns a list of images."""
    from .ocr import detect_orientation
    out = [img]
    if opts.get("split_pages") and looks_like_spread(img):
        out = split_facing(img)
    res = []
    for im in out:
        if opts.get("orientation"):
            r = detect_orientation(im)
            if r:
                im = rotate(im, r)
        if opts.get("deskew"):
            im = deskew(im)
        if opts.get("denoise"):
            im = denoise(im)
        dpi = int(im.info.get("dpi", (300, 300))[0] or 300)
        if dpi < 150 or dpi > 600:
            im = set_resolution(im, 300)
        res.append(im)
    return res
