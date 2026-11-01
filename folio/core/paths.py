"""Filesystem locations, bundled tools and clean child-process environments."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

APPDIR = os.environ.get("APPDIR")  # set by the AppImage runtime
PKG_DIR = Path(__file__).resolve().parent.parent


def _xdg(var, default):
    return Path(os.environ.get(var) or (Path.home() / default))


def config_dir() -> Path:
    p = _xdg("XDG_CONFIG_HOME", ".config") / "folio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = _xdg("XDG_DATA_HOME", ".local/share") / "folio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = _xdg("XDG_CACHE_HOME", ".cache") / "folio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_tessdata_dir() -> Path:
    p = data_dir() / "tessdata"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bundled(rel: str):
    """Path inside the AppImage (usr/...) if running from one."""
    if APPDIR:
        p = Path(APPDIR) / rel
        if p.exists():
            return p
    return None


def tesseract_bin():
    env = os.environ.get("FOLIO_TESSERACT")
    if env and Path(env).exists():
        return env
    b = bundled("usr/bin/tesseract")
    if b:
        return str(b)
    return shutil.which("tesseract")


def tessdata_sources():
    dirs = [user_tessdata_dir()]
    b = bundled("usr/share/tessdata")
    if b:
        dirs.append(b)
    for d in ("/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata",
              "/usr/share/tessdata", "/usr/local/share/tessdata"):
        if Path(d).is_dir():
            dirs.append(Path(d))
    return dirs


_merged = None


def merged_tessdata() -> Path:
    """Tesseract accepts only one tessdata dir, so we build a merged view of symlinks."""
    global _merged
    if _merged is not None:
        return _merged
    out = cache_dir() / "tessdata"
    if out.exists():
        for f in out.iterdir():
            if f.is_symlink() or f.is_file():
                f.unlink()
    out.mkdir(parents=True, exist_ok=True)
    for d in tessdata_sources():
        for f in sorted(Path(d).iterdir()):
            target = out / f.name
            if target.exists() or target.is_symlink():
                continue  # earlier dirs win (user > bundled > system)
            if f.suffix == ".traineddata" or f.name in ("configs", "tessconfigs", "pdf.ttf"):
                try:
                    target.symlink_to(f.resolve())
                except OSError:
                    pass
    _merged = out
    return out


_CHILD_STRIP = ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "TESSDATA_PREFIX", "PYTHONNOUSERSITE",
                "GDK_PIXBUF_MODULE_FILE", "FOLIO_APP")


def external_env():
    """Environment for launching *system* programs (xdg-open, libreoffice...) from inside an AppImage."""
    env = dict(os.environ)
    for k in _CHILD_STRIP:
        orig = env.pop("FOLIO_ORIG_" + k, None)
        env.pop(k, None)
        if orig:
            env[k] = orig
    if APPDIR and "PATH" in env:
        env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if not p.startswith(APPDIR))
    return env


def run_external(args, wait=False, **kw):
    if wait:
        return subprocess.run(args, env=external_env(), **kw)
    return subprocess.Popen(args, env=external_env(), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def which_external(name):
    return shutil.which(name, path=external_env().get("PATH"))


def open_with_default(path):
    run_external(["xdg-open", str(path)])


def sibling_command(app: str):
    """Command line that starts another Folio app (works from source and from any of our AppImages).

    Inside an AppImage we re-run the AppImage file itself, so the new app gets its own mount and keeps
    running after this one exits."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).exists():
        return [appimage, app]
    return [sys.executable, "-m", "folio", app]


def launch_sibling(app: str, *files):
    if os.environ.get("APPIMAGE"):
        env = external_env()
    else:
        env = dict(os.environ)
        env.pop("FOLIO_APP", None)
    subprocess.Popen(sibling_command(app) + [str(f) for f in files], env=env, start_new_session=True)


def install_desktop_entry(app_id):
    """Add this app (AppImage) to the desktop's application menu."""
    from .. import APPS
    name, binary, comment = APPS[app_id]
    exe = os.environ.get("APPIMAGE")
    cmd = f'"{exe}"' if exe else " ".join(sibling_command(app_id))
    apps = _xdg("XDG_DATA_HOME", ".local/share") / "applications"
    icons = _xdg("XDG_DATA_HOME", ".local/share") / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    src = PKG_DIR / "resources" / "icons" / f"{binary}.svg"
    if src.exists():
        (icons / f"{binary}.svg").write_bytes(src.read_bytes())
    mime = {"reader": "MimeType=application/pdf;\n", "ocr": "MimeType=image/png;image/jpeg;image/tiff;\n"}.get(app_id, "")
    f = apps / f"{binary}.desktop"
    f.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nComment={comment}\nExec={cmd} %F\n"
                 f"Icon={binary}\nTerminal=false\nCategories=Office;Graphics;\nStartupWMClass={binary}\n{mime}")
    return f
