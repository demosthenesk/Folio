# Folio feature list

Complete list of what each Folio app does, menu by menu.

## Folio Reader (`folio-reader`)

**Start window** — tabs Open / Scan / Compare / Recent.
- Open: Open PDF Document, Open in OCR Editor, Convert to PDF, Convert to Microsoft Word,
  Convert to Microsoft Excel, Convert to Other Formats, New PDF Document.
- Scan: Scan to OCR Editor, Scan to PDF, Scan to Microsoft Word, Scan to Microsoft Excel,
  Scan to Image File, Scan to Other Formats.
- Compare: open Folio Compare, recent comparisons.
- Recent: recent PDF documents and OCR projects, with search.
- OCR-language selector and Options.

**PDF editor**
- File: New Task, Open, Save, Save As PDF / Searchable PDF / Image-only PDF / DOCX / XLSX / PPTX / ODT /
  HTML / EPUB / FB2 / DjVu* / RTF / TXT / CSV / Page Images, Close, Recognize Document (invisible OCR
  text layer), Compare Documents, Password Security (AES-256, AES-128, RC4-128, permissions),
  Reduce File Size, E-mail, Print, Document Properties, Recent Files.
- Edit: Undo/Redo, Copy, Copy As Image/Text/Table, Select All, Add Pages (from file, scanner, blank),
  Rotate Pages, Correct Orientation of All Pages, Crop Pages, Enhance Page Images, Replace Pages,
  Delete Pages, Extract Pages, Add Picture, Add Bookmark, Add Link, Add Attachment, Find / Find Next,
  Form Data (load, save, reset; fill fields by clicking), Delete Objects and Data,
  Delete Comments and Markup.
- View: navigation pane (Pages, Bookmarks, Search, Attachments), PDF Tools bar, Comments pane, zoom
  (in, out, best fit, actual size, fit width), Go to Page (number, next, previous, first, last, back,
  forward), one-page / two-page scrolling, full screen, Highlight Fields.
- Tools: Select / Hand, Comments and Markup (note, highlight, underline, strikethrough, insert text,
  search and mark up), Drawing (ink, line, arrow, rectangle, oval, text box), Edit Text, Erase,
  Stamps (14 standard, text stamps, image stamps), Watermark (add / delete), Header and Footer
  (page numbers, Bates numbers, date; add / delete), Signature (image or text), Redaction mode and
  Apply Redactions, OCR Editor, Compare, Hot Folder, Folio Snap, Options.

## Folio OCR Editor (`folio-ocr`)

- File: New Task, Open Image, Scan Pages, Save As (all formats + page images), Send To (PDF editor,
  word processor, Word document, spreadsheet, presentation, web browser, clipboard, EPUB),
  New / Open / Save OCR Project (`*.folio`), E-mail document or images, Print text or images,
  recent projects.
- Edit: Undo/Redo, Cut / Copy / Copy Area as Image / Paste / Delete, Delete Pages, Delete Areas and Text,
  Delete Text, Select all / even / odd pages, Reorder Pages (reverse, duplex order), Rotate / Flip,
  Crop, Split, Edit Page Images, Find, Find Next, Replace, Insert Symbol.
- View: Pages pane, Zoom pane, image only / image and text / text only, zoom, Go to Page,
  Page Properties.
- Recognize: Recognize All Pages / Page, Analyze All Pages / Page, Verify Text (word image, Confirm,
  Confirm All, Add to Dictionary, Replace, Skip), Next / Previous Error, Mark Text as Verified.
- Area: Recognize Area, Analyze Area, Change Area Type, Draw Text / Table / Picture / Background /
  Barcode / Recognition areas (movable, resizable, numbered), Delete Areas, Reorder Areas,
  Analyze Table Structure, Save / Load Area Template.
- Image editor pane: recommended preprocessing, deskew, rotate / flip / free rotation, split, crop,
  brightness and contrast, levels, invert, photo correction, remove colour marks, remove noise,
  black-and-white / grayscale, eraser, correct resolution — for the current, selected or all pages.
- Tools: User Dictionary, Redaction Mode, Hot Folder (watch a folder and convert new files),
  Compare Documents, Folio Snap, Options (General, Image Processing, Recognition — PDF mode,
  thorough / fast, uncertainty threshold — and Format Settings).

## Folio Compare (`folio-compare`)

- Two document panes and a DIFFERENCES pane.
- Options: document language, find differences in punctuation, find one-letter differences,
  headers and footers.
- Compare; next / previous / ignore / copy difference; select all; restore ignored differences.
- Synchronised scrolling; zoom; PDF recognition mode (OCR / automatic / text only).
- Difference categories: body text, headers and footers, numbering.
- Save: PDF with comments (document 1 or 2), DOCX with tracked changes (document 1 or 2),
  comparison report (DOCX / HTML / TXT). Recent comparisons.
- Inputs: PDF, images, DOCX / ODT / RTF and other office files*, TXT.

## Folio Snap (`folio-snap`)

- Main window: Capture (Area, Window, Screen, Timed Screen), Language, Send, Go!.
- Send targets: Text to Clipboard, Table to Clipboard, Image to Clipboard, Text / Table / Image to File,
  Text to Word Processor, Table to Spreadsheet, Image to OCR Editor, Image to E-mail.
- Tray menu: Text / Image to Clipboard, Show window, Options (Always on Top, Run at Startup, Sounds,
  Keyboard Shortcut help), Help, Exit.
- Command line for desktop keyboard shortcuts: `--go`, `--capture area|window|screen|timed`,
  `--send "<target>"`, `--lang eng+ell`, `--tray`.
- Capture uses the xdg-desktop-portal on Wayland and Qt on X11.

## Not included yet

Certificate-based digital signatures, MRC compression, PDF/A validation, OCR pattern training,
style editor, multi-step automated task scripts.

\* DjVu needs `djvulibre-bin`; office documents need LibreOffice; scanning needs `sane-utils`.
