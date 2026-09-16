from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.static_site import assert_static_site_complete


def main() -> None:
    site_dir = Path(os.environ.get("SITE_DIR", "site"))
    state = assert_static_site_complete(
        site_dir,
        expected_base_path=os.environ.get("PUBLIC_BASE_PATH"),
    )
    print(json.dumps(state.to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    main()
