import os
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    app = os.environ.get("FOLIO_APP", "")
    if argv and argv[0] in ("reader", "ocr", "compare", "snap", "pdf"):
        app = argv.pop(0)
    if argv and argv[0] in ("--version", "-V"):
        from folio import __version__
        print(__version__)
        return 0
    app = app or "reader"
    if argv and argv[0] == "--install-desktop":
        from folio.core.paths import install_desktop_entry
        print("Installed", install_desktop_entry("reader" if app == "pdf" else app))
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print("Usage: folio-<app> [files...]\n  --install-desktop   add this app to the applications menu\n"
              "  --version           print version\nApps: reader, ocr, compare, snap (snap also: --go, --capture, --send, --lang)")
        return 0
    if app in ("reader", "pdf"):
        from folio.apps.reader import run
    elif app == "ocr":
        from folio.apps.ocreditor import run
    elif app == "compare":
        from folio.apps.comparator import run
    elif app == "snap":
        from folio.apps.snap import run
    else:
        print("unknown app", app)
        return 2
    return run(argv, pdf_mode=(app == "pdf")) if app in ("reader", "pdf") else run(argv)


if __name__ == "__main__":
    sys.exit(main())
