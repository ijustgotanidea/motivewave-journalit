#!/usr/bin/env python3
"""Build the plugin and copy it into an Obsidian vault.

    python scripts/install.py "C:/Users/you/Documents/My Vault"

Then, in Obsidian: Settings → Community plugins → enable "MotiveWave → Journalit"
(restart Obsidian first if it was open). Re-running updates the plugin and keeps
its settings (data.json).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from build import build  # noqa: E402

PLUGIN_ID = "motivewave-journalit"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    vault = Path(sys.argv[1]).expanduser().resolve()
    if not (vault / ".obsidian").is_dir():
        print(f"{vault} is not an Obsidian vault (no .obsidian folder).")
        return 1
    if not (vault / ".obsidian" / "plugins" / "journalit").is_dir():
        print("warning: the Journalit plugin is not installed in this vault; install it first.")
    dist = build()
    target = vault / ".obsidian" / "plugins" / PLUGIN_ID
    target.mkdir(parents=True, exist_ok=True)
    for name in ("main.js", "manifest.json"):
        shutil.copyfile(dist / name, target / name)
    print(f"installed into {target}")
    print('Next: restart Obsidian, then Settings → Community plugins → enable "MotiveWave → Journalit".')
    return 0


if __name__ == "__main__":
    sys.exit(main())
