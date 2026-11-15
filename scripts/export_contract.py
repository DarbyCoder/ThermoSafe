"""Export response/input schemas without loading or exporting local secrets."""

import json
from pathlib import Path

from backend.config import Settings
from backend.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = create_app(Settings("documentation-device", "documentation-dashboard", ROOT / "data/unused.sqlite3"))
    path = ROOT / "docs/openapi.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(app.openapi(), indent=2)+"\n")
    print("Exported docs/openapi.json")


if __name__ == "__main__":
    main()
