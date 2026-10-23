#!/usr/bin/env python3
"""Build the plugin: dist/main.js (with the Python engine embedded) and dist/manifest.json.

    python build.py

Those two files are the whole plugin: upload them to a GitHub release, or copy
them into <vault>/.obsidian/plugins/motivewave-journalit/ (scripts/install.py
does that). Standard library only.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "__ENGINE_SOURCE__"


def build() -> Path:
    source = (HERE / "src" / "main.js").read_text(encoding="utf-8")
    engine = (HERE / "src" / "motivewave_journalit.py").read_text(encoding="utf-8")
    if source.count(PLACEHOLDER) != 1:
        raise SystemExit(f"src/main.js must contain {PLACEHOLDER} exactly once")
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    with open(dist / "main.js", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(source.replace(PLACEHOLDER, json.dumps(engine)))
    shutil.copyfile(HERE / "manifest.json", dist / "manifest.json")
    return dist


if __name__ == "__main__":
    out = build()
    version = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))["version"]
    print(f"built {version}: {out / 'main.js'}, {out / 'manifest.json'}")
    sys.exit(0)
