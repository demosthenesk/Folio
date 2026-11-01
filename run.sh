#!/bin/sh
# Run a Folio app from source: ./run.sh reader|ocr|compare|snap [files...]
cd "$(dirname "$0")"
PY="${FOLIO_PYTHON:-$HOME/.venvs/folio/bin/python}"
[ -x "$PY" ] || PY=python3
exec "$PY" -m folio "$@"
