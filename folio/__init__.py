"""Folio — an OCR & PDF suite for Linux."""
__version__ = "1.0.1"

SUITE = "Folio"

# app-id -> (display name, binary name, short description)
APPS = {
    "reader": ("Folio Reader", "folio-reader", "Start tasks, open, edit and convert PDF documents"),
    "ocr": ("Folio OCR Editor", "folio-ocr", "Recognize scans, photos and PDFs and edit the text"),
    "compare": ("Folio Compare", "folio-compare", "Find differences between two versions of a document"),
    "snap": ("Folio Snap", "folio-snap", "Capture text, tables and images from the screen"),
}
