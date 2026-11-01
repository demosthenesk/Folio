"""Small JSON-backed settings store shared by all Folio apps."""
import json
import threading

from .paths import config_dir

DEFAULTS = {
    "languages": ["eng"],
    "recognition_mode": "thorough",      # thorough | fast
    "pdf_mode": "auto",                  # auto | ocr | text
    "preprocess": {"deskew": True, "orientation": False, "split_pages": False, "denoise": False},
    "uncertain_threshold": 60,
    "ocr_dpi": 300,
    "keep_line_breaks": False,
    "keep_page_breaks": True,
    "export_images": True,
    "pdf_image_quality": "balanced",     # high | balanced | compact
    "ocr_startup": "new",                # new | last
    "last_project": "",
    "recent_documents": [],
    "recent_comparisons": [],
    "user_words": [],
    "compare": {"punctuation": False, "one_letter": False, "headers": True, "language": "eng"},
    "snap": {"capture": "Area", "send": "Text to Clipboard", "language": "eng", "always_on_top": False,
              "sounds": False, "show_window": True},
    "scanner": {"device": "", "resolution": 300, "mode": "Color"},
    "hot_folder": {"input": "", "output": "", "format": "pdf", "interval": 10},
}

_lock = threading.Lock()


class Settings:
    def __init__(self):
        self.path = config_dir() / "settings.json"
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            stored = json.loads(self.path.read_text("utf-8"))
            for k, v in stored.items():
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        except Exception:
            pass

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, v):
        self.data[k] = v
        self.save()

    def get(self, k, default=None):
        return self.data.get(k, default)

    def save(self):
        with _lock:
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), "utf-8")
                tmp.replace(self.path)
            except Exception:
                pass

    def add_recent(self, key, path, limit=20):
        path = str(path)
        lst = [p for p in self.data.get(key, []) if p != path]
        lst.insert(0, path)
        self.data[key] = lst[:limit]
        self.save()


_settings = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
