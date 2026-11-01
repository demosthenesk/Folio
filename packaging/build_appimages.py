#!/usr/bin/env python3
"""Build the four Folio AppImages (Ubuntu 24.04 / x86_64).

Usage:
  python3 packaging/build_appimages.py --version 1.0.0 --out ../appimages \
      --site-packages /path/to/venv/lib/python3.12/site-packages \
      --appimagetool /path/to/appimagetool --runtime /path/to/runtime-x86_64

Each AppImage is self-contained: CPython 3.12, Qt 6 (PySide6), PyMuPDF, Tesseract 5 + language data.
All four share the same payload; the AppRun picks the app (reader / ocr / compare / snap).
Output goes to <out>/v<version>/Folio-<App>-<version>-x86_64.AppImage
"""
import argparse
import compileall
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = "python3.12"

APPS = [  # app id, file stem, binary, name, comment, categories, mime
    ("reader", "Reader", "folio-reader", "Folio Reader", "Open, edit, convert and recognize PDF documents",
     "Office;Viewer;Graphics;", "application/pdf;"),
    ("ocr", "OCR-Editor", "folio-ocr", "Folio OCR Editor", "Recognize text in scans, photos and PDFs",
     "Office;Graphics;Scanning;OCR;", "image/png;image/jpeg;image/tiff;application/pdf;"),
    ("compare", "Compare", "folio-compare", "Folio Compare", "Find differences between two versions of a document",
     "Office;Utility;", ""),
    ("snap", "Snap", "folio-snap", "Folio Snap", "Capture text, tables and images from the screen",
     "Utility;Graphics;OCR;", ""),
]

# never bundle these: they must come from the host system (driver / desktop integration / glibc)
EXCLUDE = re.compile(r"^(ld-linux.*|libc\.so.*|libm\.so.*|libdl\.so.*|libpthread\.so.*|librt\.so.*|libresolv\.so.*|"
                     r"libutil\.so.*|libnsl\.so.*|libanl\.so.*|libstdc\+\+\.so.*|libgcc_s\.so.*|libGL.*|libEGL.*|"
                     r"libGLX.*|libGLdispatch.*|libOpenGL.*|libdrm.*|libgbm.*|libX11.*|libXext.*|libXrender.*|libXi\.so.*|"
                     r"libXau.*|libXdmcp.*|libxcb\.so.*|libxcb-(?!cursor).*|libX11-xcb.*|libwayland-.*|libfontconfig.*|"
                     r"libfreetype.*|libharfbuzz.*|libexpat.*|libz\.so.*|libdbus-1.*|libglib-2.*|libgobject-2.*|"
                     r"libgio-2.*|libgmodule-2.*|libgthread-2.*|libxkbcommon.*|libudev.*|libsystemd.*|libselinux.*|"
                     r"libpcre2.*|libmount.*|libblkid.*|libcap\.so.*|libasound.*|libpulse.*|libcups.*|"
                     r"libgnutls.*|libp11-kit.*|libkrb5.*|libk5crypto.*|libgssapi.*|libcom_err.*|libkeyutils.*|"
                     r"libkrb5support.*|libtasn1.*|libhogweed.*|libnettle.*|libgmp\.so.*|libidn2.*|libunistring.*|"
                     r"libgtk-3.*|libgdk-3.*|libgdk_pixbuf.*|libatk.*|libatspi.*|libcairo.*|libpango.*|libepoxy.*|"
                     r"libthai.*|libdatrie.*|libfribidi.*|libgraphite2.*|libpixman.*|libX[a-z].*|libavahi.*|"
                     r"libgcrypt.*|libgpg-error.*|libbsd\.so.*|libmd\.so.*|libicu.*|libxml2.*|libcrypt\.so.*)$")

SITE_KEEP = ["PIL", "pillow.libs", "PySide6", "shiboken6", "pymupdf", "numpy", "numpy.libs", "docx", "et_xmlfile",
             "lxml", "odf", "openpyxl", "pptx", "xlsxwriter", "typing_extensions.py", "jeepney", "defusedxml"]
PYSIDE_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtSvg", "QtPrintSupport", "QtDBus"]
QT_PLUGIN_DIRS = ["platforms", "platformthemes", "imageformats", "iconengines", "printsupport", "xcbglintegrations",
                  "wayland-decoration-client", "wayland-graphics-integration-client", "wayland-shell-integration",
                  "platforminputcontexts"]
QT_PLUGIN_DROP = ["libqgtk3.so", "libqtvirtualkeyboardplugin.so", "libqpdf.so"]
TESS_LANGS = ["eng", "ell", "grc", "deu", "fra", "spa", "ita", "rus", "osd"]


def run(*a, **kw):
    print("+", " ".join(map(str, a)))
    return subprocess.run([str(x) for x in a], check=True, **kw)


def ldd(path):
    p = subprocess.run(["ldd", str(path)], capture_output=True, text=True)
    out = {}
    for line in p.stdout.splitlines():
        m = re.match(r"\s*(\S+)\s+=>\s+(/\S+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def bundle_deps(binaries, libdir: Path, skip_names=()):
    libdir.mkdir(parents=True, exist_ok=True)
    done = set()
    todo = list(binaries)
    while todo:
        b = todo.pop()
        for name, path in ldd(b).items():
            if name in done or EXCLUDE.match(name) or name in skip_names:
                continue
            done.add(name)
            dst = libdir / name
            if not dst.exists():
                shutil.copy2(os.path.realpath(path), dst)
                dst.chmod(0o755)
                todo.append(dst)
    return done


def build_base(args, base: Path):
    if base.exists():
        shutil.rmtree(base)
    usr = base / "usr"
    (usr / "bin").mkdir(parents=True)
    # --- CPython 3.12 (the Ubuntu 24.04 build)
    shutil.copy2(f"/usr/bin/{PY}", usr / "bin" / PY)
    std = usr / "lib" / PY
    shutil.copytree(f"/usr/lib/{PY}", std, symlinks=False, ignore=shutil.ignore_patterns(
        "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "__pycache__",
        "config-3.12-*", "dist-packages", "site-packages", "*.exe", "pydoc_data", "venv"))
    # --- site-packages
    sp = std / "site-packages"
    sp.mkdir()
    src = Path(args.site_packages)
    for n in SITE_KEEP:
        s = src / n
        if s.is_dir():
            shutil.copytree(s, sp / n, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyi", "tests"))
        elif s.exists():
            shutil.copy2(s, sp / n)
        else:
            print("WARNING: missing", n)
    # prune PyMuPDF extras
    for extra in ("layout", "mupdf-devel"):
        shutil.rmtree(sp / "pymupdf" / extra, ignore_errors=True)
    prune_pyside(sp / "PySide6")
    # --- Folio code
    shutil.copytree(ROOT / "folio", usr / "share" / "folio" / "folio", ignore=shutil.ignore_patterns("__pycache__"))
    # --- Tesseract + language data
    shutil.copy2("/usr/bin/tesseract", usr / "bin" / "tesseract")
    td = usr / "share" / "tessdata"
    td.mkdir(parents=True)
    sysdata = Path("/usr/share/tesseract-ocr/5/tessdata")
    for l in TESS_LANGS:
        for d in [Path(args.extra_tessdata or "/nonexistent"), sysdata]:
            f = d / f"{l}.traineddata"
            if f.exists():
                shutil.copy2(f, td / f.name)
                break
        else:
            print("WARNING: no tessdata for", l)
    for n in ("configs", "tessconfigs"):
        if (sysdata / n).is_dir():
            shutil.copytree(sysdata / n, td / n)
    if (sysdata / "pdf.ttf").exists():
        shutil.copy2(sysdata / "pdf.ttf", td / "pdf.ttf")
    # --- fonts for PDF text layers
    fd = usr / "share" / "fonts"
    fd.mkdir(parents=True)
    for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSerif.ttf"):
        p = Path("/usr/share/fonts/truetype/dejavu") / f
        if p.exists():
            shutil.copy2(p, fd / f)
    lic = Path("/usr/share/doc/fonts-dejavu-core/copyright")
    if lic.exists():
        shutil.copy2(lic, fd / "DejaVu-LICENSE")
    # --- shared libraries
    bins = [usr / "bin" / PY, usr / "bin" / "tesseract"]
    bins += list((std / "lib-dynload").glob("*.so"))
    qt = sp / "PySide6" / "Qt"
    bins += list((qt / "plugins").rglob("*.so"))
    bins += [p for p in (qt / "lib").glob("*.so*")]
    bins += list((sp / "PySide6").glob("*.so")) + list((sp / "shiboken6").glob("*.so*"))
    bins += list((sp / "pymupdf").glob("*.so*")) + list(sp.glob("PIL/*.so")) + list(sp.glob("lxml/*.so"))
    bins += list(sp.glob("numpy/**/*.so"))
    qt_names = {p.name for p in (qt / "lib").iterdir()} | {p.name for p in (sp / "shiboken6").iterdir()} | \
               {p.name for p in (sp / "pymupdf").iterdir()} | {p.name for p in (sp / "PySide6").iterdir()}
    for d in ("pillow.libs", "numpy.libs"):
        qt_names |= {p.name for p in (sp / d).iterdir()}
    bundled = bundle_deps(bins, usr / "lib", skip_names=qt_names)
    print("bundled system libraries:", len(bundled))
    # licenses / docs
    (usr / "share" / "doc" / "folio").mkdir(parents=True)
    for f in ("README.md", "LICENSE"):
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, usr / "share" / "doc" / "folio" / f)
    # --- precompile bytecode (the AppImage is read-only)
    compileall.compile_dir(str(std), quiet=1, workers=0, legacy=False, ddir=None)
    compileall.compile_dir(str(usr / "share" / "folio"), quiet=1, workers=0)


def prune_pyside(ps: Path):
    keep_mods = set(PYSIDE_MODULES)
    for f in list(ps.iterdir()):
        if f.is_dir():
            if f.name not in ("Qt", "support"):
                shutil.rmtree(f)
        elif f.name.endswith(".abi3.so") and f.name.split(".")[0] not in keep_mods:
            f.unlink()
        elif f.suffix == ".pyi":
            f.unlink()
    qt = ps / "Qt"
    for d in ("qml", "translations", "metatypes", "libexec", "resources"):
        shutil.rmtree(qt / d, ignore_errors=True)
    plugins = qt / "plugins"
    for d in list(plugins.iterdir()):
        if d.name not in QT_PLUGIN_DIRS:
            shutil.rmtree(d)
    for f in list(plugins.rglob("*.so")):
        if f.name in QT_PLUGIN_DROP:
            f.unlink()
    for d in ("platforms",):
        for f in (plugins / d).iterdir():
            if not any(k in f.name for k in ("xcb", "wayland", "offscreen", "minimal")):
                f.unlink()
    # keep only the Qt libraries needed by the kept modules + plugins
    libdir = qt / "lib"
    needed = set()
    roots = [ps / f"{m}.abi3.so" for m in keep_mods] + list(plugins.rglob("*.so"))
    roots += list(ps.glob("libpyside6*.so*"))
    todo = list(roots)
    seen = set()
    while todo:
        b = todo.pop()
        if b in seen:
            continue
        seen.add(b)
        for name, path in ldd(b).items():
            cand = libdir / name
            if cand.exists() and name not in needed:
                needed.add(name)
                todo.append(cand)
    for f in libdir.iterdir():
        if f.name not in needed and f.is_file() and not f.is_symlink():
            if not any(f.name.startswith(n.split(".so")[0] + ".so") for n in needed):
                f.unlink()
    for f in libdir.iterdir():
        if f.is_symlink() and not f.exists():
            f.unlink()


APPRUN = r"""#!/bin/sh
# Folio AppRun — starts the @NAME@ (app id: @APP@)
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="$HERE"
for v in LD_LIBRARY_PATH PYTHONHOME PYTHONPATH QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH TESSDATA_PREFIX; do
  eval "val=\${$v}"
  [ -n "$val" ] && export "FOLIO_ORIG_$v=$val"
done
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/share/folio:$HERE/usr/lib/python3.12/site-packages"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QT_PLUGIN_PATH="$HERE/usr/lib/python3.12/site-packages/PySide6/Qt/plugins"
unset QT_QPA_PLATFORM_PLUGIN_PATH
if [ -z "$QT_QPA_PLATFORM" ] && [ -n "$WAYLAND_DISPLAY" ]; then export QT_QPA_PLATFORM="wayland;xcb"; fi
export FOLIO_APP="@APP@"
exec "$HERE/usr/bin/python3.12" -s -m folio "$@"
"""


def desktop_entry(app):
    aid, stem, binary, name, comment, cats, mime = app
    s = (f"[Desktop Entry]\nType=Application\nName={name}\nComment={comment}\nExec={binary} %F\nIcon={binary}\n"
         f"Categories={cats}\nTerminal=false\nStartupWMClass={binary}\n")
    if mime:
        s += f"MimeType={mime}\n"
    return s


def build_app(args, base: Path, app, outdir: Path, work: Path):
    aid, stem, binary, name, *_ = app
    ad = work / f"{binary}.AppDir"
    if ad.exists():
        shutil.rmtree(ad)
    run("cp", "-al", base, ad)
    (ad / "AppRun").write_text(APPRUN.replace("@APP@", aid).replace("@NAME@", name))
    (ad / "AppRun").chmod(0o755)
    (ad / f"{binary}.desktop").write_text(desktop_entry(app))
    icon = ROOT / "folio" / "resources" / "icons" / f"{binary}.svg"
    shutil.copy2(icon, ad / f"{binary}.svg")
    png = work / f"{binary}.png"
    subprocess.run([str(base / "usr" / "bin" / PY), "-c", ICON_PNG, str(icon), str(png)],
                   env={"PYTHONHOME": str(base / "usr"), "QT_QPA_PLATFORM": "offscreen",
                        "PYTHONPATH": str(base / "usr" / "lib" / PY / "site-packages"),
                        "LD_LIBRARY_PATH": str(base / "usr" / "lib")}, check=True)
    shutil.copy2(png, ad / ".DirIcon")
    hic = ad / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    hic.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon, hic / f"{binary}.svg")
    apps = ad / "usr" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / f"{binary}.desktop").write_text(desktop_entry(app))
    out = outdir / f"Folio-{stem}-{args.version}-x86_64.AppImage"
    env = dict(os.environ, ARCH="x86_64", VERSION=args.version, APPIMAGE_EXTRACT_AND_RUN="1")
    run(args.appimagetool, "--runtime-file", args.runtime, "--comp", "zstd", "--mksquashfs-opt", "-Xcompression-level",
        "--mksquashfs-opt", "19", "--no-appstream", ad, out, env=env)
    shutil.rmtree(ad)
    return out


ICON_PNG = """
import sys
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication(['x'])
r = QSvgRenderer(sys.argv[1]); im = QImage(256, 256, QImage.Format_ARGB32); im.fill(Qt.transparent)
p = QPainter(im); r.render(p); p.end(); im.save(sys.argv[2])
"""


def main():
    if sys.version_info[:2] != (3, 12):
        sys.exit("Run this script with Python 3.12 (the bundled interpreter version) so bytecode matches.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--site-packages", required=True)
    ap.add_argument("--appimagetool", required=True)
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--extra-tessdata", help="folder with additional *.traineddata (e.g. ell, grc)")
    ap.add_argument("--work", default="/tmp/folio-build")
    ap.add_argument("--apps", default="reader,ocr,compare,snap")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    base = work / "base"
    build_base(a, base)
    outdir = Path(a.out) / f"v{a.version}"
    outdir.mkdir(parents=True, exist_ok=True)
    built = []
    for app in APPS:
        if app[0] in a.apps.split(","):
            built.append(build_app(a, base, app, outdir, work))
    for b in built:
        print(f"{b}  {b.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
