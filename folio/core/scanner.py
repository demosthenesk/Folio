"""Scanner access through SANE's `scanimage` command (sudo apt install sane-utils)."""
import io
import re
import subprocess

from PIL import Image

from . import paths


class ScanError(RuntimeError):
    pass


def available():
    return bool(paths.which_external("scanimage"))


def list_devices(timeout=30):
    if not available():
        return []
    p = paths.run_external(["scanimage", "-f", "%d\t%v %m%n"], wait=True, capture_output=True, timeout=timeout)
    devs = []
    for line in p.stdout.decode(errors="replace").splitlines():
        if "\t" in line:
            d, label = line.split("\t", 1)
            devs.append((d.strip(), label.strip()))
    return devs


def scan(device="", resolution=300, mode="Color", timeout=300) -> Image.Image:
    if not available():
        raise ScanError("Scanning requires SANE (sudo apt install sane-utils).")
    args = ["scanimage", "--format=png", f"--resolution={int(resolution)}", f"--mode={mode}"]
    if device:
        args.insert(1, f"--device-name={device}")
    p = paths.run_external(args, wait=True, capture_output=True, timeout=timeout)
    if p.returncode != 0 or not p.stdout:
        raise ScanError(p.stderr.decode(errors="replace").strip() or "Scanning failed.")
    img = Image.open(io.BytesIO(p.stdout))
    img.load()
    img.info["dpi"] = (int(resolution), int(resolution))
    return img
