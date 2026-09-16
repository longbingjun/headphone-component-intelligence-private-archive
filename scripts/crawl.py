"""爬虫入口（v2）：转发到 crawl_v2。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.crawl_v2 import main  # noqa: E402

if __name__ == "__main__":
    main()
