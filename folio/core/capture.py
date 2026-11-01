"""Screen capture back-ends for Folio Snap (X11 and Wayland).

Order of preference for a full-screen grab:
  1. xdg-desktop-portal Screenshot (Wayland & X11 desktops, GNOME/KDE...)
  2. Qt's QScreen.grabWindow (X11 sessions)
  3. external tools: gnome-screenshot, spectacle, grim, scrot, ImageMagick import
"""
import os
import random
import string
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from . import paths


class CaptureError(RuntimeError):
    pass


def is_wayland():
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def portal_screenshot(interactive=False, timeout=120):
    """Ask xdg-desktop-portal for a screenshot. Returns a PIL image."""
    try:
        from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
        from jeepney.io.blocking import open_dbus_connection, Proxy
    except Exception as e:  # pragma: no cover
        raise CaptureError(f"D-Bus support missing: {e}")
    conn = open_dbus_connection(bus="SESSION")
    try:
        token = "folio" + "".join(random.choice(string.ascii_lowercase) for _ in range(8))
        sender = conn.unique_name.lstrip(":").replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=handle)
        Proxy(message_bus, conn).AddMatch(rule)
        portal = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop",
                             interface="org.freedesktop.portal.Screenshot")
        opts = {"handle_token": ("s", token), "interactive": ("b", bool(interactive)), "modal": ("b", True)}
        with conn.filter(rule) as queue:
            reply = conn.send_and_get_reply(new_method_call(portal, "Screenshot", "sa{sv}", ("", opts)), timeout=30)
            if reply.header.message_type.name == "error":
                raise CaptureError(f"Portal error: {reply.body}")
            sig = conn.recv_until_filtered(queue, timeout=timeout)
        code, results = sig.body
        if code != 0:
            raise CaptureError("cancelled" if code == 1 else "Screenshot was not allowed by the desktop.")
        uri = results.get("uri", ("s", ""))[1]
        p = Path(unquote(urlparse(uri).path))
        img = Image.open(p)
        img.load()
        try:
            if p.parent.name in ("Screenshots",) or "/tmp" in str(p):
                p.unlink()
        except Exception:
            pass
        return img.convert("RGB")
    finally:
        conn.close()


def _tool_capture():
    out = tempfile.mktemp(prefix="folio-shot-", suffix=".png")
    cands = []
    if paths.which_external("gnome-screenshot"):
        cands.append(["gnome-screenshot", "-f", out])
    if paths.which_external("spectacle"):
        cands.append(["spectacle", "-b", "-n", "-f", "-o", out])
    if paths.which_external("grim"):
        cands.append(["grim", out])
    if paths.which_external("scrot"):
        cands.append(["scrot", "-o", out])
    if paths.which_external("import"):
        cands.append(["import", "-window", "root", out])
    for c in cands:
        try:
            paths.run_external(c, wait=True, capture_output=True, timeout=30)
            if Path(out).exists() and Path(out).stat().st_size > 0:
                img = Image.open(out)
                img.load()
                os.unlink(out)
                return img.convert("RGB")
        except Exception:
            continue
    return None


def qt_grab():
    """Grab the whole virtual desktop with Qt (works on X11)."""
    from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
    screens = QGuiApplication.screens()
    if not screens:
        return None
    geo = screens[0].virtualGeometry()
    canvas = QPixmap(geo.size())
    canvas.fill()
    p = QPainter(canvas)
    for s in screens:
        g = s.geometry()
        pm = s.grabWindow(0)
        p.drawPixmap(g.x() - geo.x(), g.y() - geo.y(), g.width(), g.height(), pm)
    p.end()
    from ..ui.qtutil import qimage_to_pil
    img = qimage_to_pil(canvas.toImage())
    # a black image means the grab was refused (Wayland)
    if img.convert("L").getextrema()[1] < 5:
        return None
    return img


def grab_screen(prefer_portal=None):
    errors = []
    if prefer_portal is None:
        prefer_portal = is_wayland()
    order = ["portal", "qt", "tools"] if prefer_portal else ["qt", "portal", "tools"]
    for m in order:
        try:
            if m == "portal":
                return portal_screenshot(False)
            if m == "qt" and not is_wayland():
                img = qt_grab()
                if img is not None:
                    return img
            if m == "tools":
                img = _tool_capture()
                if img is not None:
                    return img
        except CaptureError as e:
            if str(e) == "cancelled":
                raise
            errors.append(f"{m}: {e}")
        except Exception as e:
            errors.append(f"{m}: {e}")
    raise CaptureError("Could not capture the screen.\n" + "\n".join(errors))


def grab_window_interactive():
    """Let the user pick a window. X11: xwininfo + Qt grab; Wayland: portal's interactive dialog."""
    if not is_wayland() and paths.which_external("xwininfo"):
        p = paths.run_external(["xwininfo"], wait=True, capture_output=True, timeout=120)
        out = p.stdout.decode(errors="replace")
        import re
        m = re.search(r"Window id:\s+(0x[0-9a-fA-F]+)", out)
        if m:
            from PySide6.QtGui import QGuiApplication
            from ..ui.qtutil import qimage_to_pil
            wid = int(m.group(1), 16)
            pm = QGuiApplication.primaryScreen().grabWindow(wid)
            if not pm.isNull():
                return qimage_to_pil(pm.toImage())
    return portal_screenshot(interactive=True)
