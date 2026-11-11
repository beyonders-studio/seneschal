"""Write Hoard as the plug-in SDK reads it, for ``initiative-plugin build
--manifest <file>``, which checks the manifest against the plug-in contract.

    uv run python apps/hoard/backend/scripts/export_plugin.py plugin.json
"""

import json
import sys
from pathlib import Path

from hoard.plugin_manifest import built_plugin


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: export_plugin.py <out.json>")
    out = Path(sys.argv[1])
    out.write_text(json.dumps(built_plugin(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
