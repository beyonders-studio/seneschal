"""Write Hoard's API schema, which the frontend generates its client from with
Orval: to the path given, or to ``apps/hoard/frontend/openapi.json``. It needs
no database and no settings.

    uv run python apps/hoard/backend/scripts/export_openapi.py [<output-path>]
"""

import json
import sys
from pathlib import Path

from hoard.openapi import openapi_schema

OUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(openapi_schema(), indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
