"""华为等品牌官网产品页抓取。"""

from sources.base import SourceDescriptor
from sources.official.fetcher import fetch_official_page, resolve_official_url, search_official_site

OFFICIAL_SOURCE = SourceDescriptor("official_consumer", "official", "品牌官网产品页", active=True)

__all__ = ["OFFICIAL_SOURCE", "fetch_official_page", "resolve_official_url", "search_official_site"]
