"""JD Union Open Platform API (search + SKU detail prices).

Credentials in project root `.env`:
  JD_UNION_APP_KEY, JD_UNION_APP_SECRET
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

_API_URL = "https://api.jd.com/routerjson"
_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    raw = env_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


def _credentials() -> tuple[str, str] | None:
    _load_dotenv()
    key = os.environ.get("JD_UNION_APP_KEY", "").strip()
    secret = os.environ.get("JD_UNION_APP_SECRET", "").strip()
    if key and secret:
        return key, secret
    return None


def union_configured() -> bool:
    return _credentials() is not None


@dataclass
class JdUnionHit:
    sku_id: str
    title: str
    price_cny: float | None = None
    msrp_cny: float | None = None
    channel_url: str = ""
    shop_hint: str = "JD Union API"

    def to_dict(self) -> dict:
        return {
            "sku_id": self.sku_id,
            "title": self.title,
            "price_cny": self.price_cny,
            "msrp_cny": self.msrp_cny,
            "channel_url": self.channel_url,
            "shop_hint": self.shop_hint,
        }


class JdUnionClient:
    def __init__(self, app_key: str, app_secret: str) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self._last_call = 0.0

    def _throttle(self, min_interval: float = 0.35) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call = time.monotonic()

    def _sign(self, params: dict[str, str]) -> str:
        parts = "".join(f"{k}{v}" for k, v in sorted(params.items()))
        digest = hashlib.md5((self.app_secret + parts + self.app_secret).encode()).hexdigest()
        return digest.upper()

    def _request(self, method: str, biz: dict) -> dict:
        self._throttle()
        params: dict[str, str] = {
            "method": method,
            "app_key": self.app_key,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "1.0",
            "sign_method": "md5",
            "360buy_param_json": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
        }
        params["sign"] = self._sign(params)
        resp = requests.post(_API_URL, data=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if "error_response" in payload:
            err = payload["error_response"]
            code = err.get("code", "?")
            msg = err.get("zh_desc") or err.get("en_desc") or err.get("msg") or str(err)
            raise RuntimeError(f"JD Union API error {code}: {msg}")
        return payload

    @staticmethod
    def _unwrap(method: str, payload: dict) -> dict:
        base = method.replace(".", "_")
        outer = (
            payload.get(f"{base}_response")
            or payload.get(f"{base}_responce")  # JD typo in some responses
            or {}
        )
        if not isinstance(outer, dict):
            return {"raw": outer}

        for field in ("queryResult", "result", "getResult"):
            if field not in outer:
                continue
            inner = outer[field]
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except json.JSONDecodeError:
                    continue
            if isinstance(inner, dict):
                code = inner.get("code")
                if code not in (None, 200, "200", 0, "0"):
                    msg = inner.get("message") or inner.get("msg") or str(inner)
                    raise RuntimeError(
                        f"JD Union API business error {code}: {msg} "
                        f"(apply interface permission in union.jd.com -> My Tools -> API)"
                    )
                return inner
            return {"raw": inner}
        return outer

    @staticmethod
    def _parse_prices(item: dict) -> tuple[float | None, float | None]:
        def _f(val) -> float | None:
            if val is None or val == "":
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        msrp = _f(item.get("price") or item.get("originPrice") or item.get("originalPrice"))
        promo = _f(
            item.get("lowestCouponPrice")
            or item.get("lowestPrice")
            or item.get("promotionPrice")
            or item.get("discountPrice")
        )
        price = promo if promo is not None else msrp
        return price, msrp

    def search_goods(self, keyword: str, *, page_index: int = 1, page_size: int = 20) -> list[JdUnionHit]:
        biz = {
            "goodsReqDTO": {
                "keyword": keyword,
                "pageIndex": page_index,
                "pageSize": page_size,
                "sceneId": 1,
            }
        }
        raw = self._request("jd.union.open.goods.query", biz)
        result = self._unwrap("jd.union.open.goods.query", raw)
        data = result.get("data") or []
        if isinstance(data, dict):
            data = data.get("goodsList") or data.get("list") or []
        hits: list[JdUnionHit] = []
        for item in data or []:
            sku = str(item.get("skuId") or item.get("sku_id") or "")
            if not sku:
                continue
            price, msrp = self._parse_prices(item)
            title = str(item.get("skuName") or item.get("title") or item.get("productName") or "")
            hits.append(
                JdUnionHit(
                    sku_id=sku,
                    title=title,
                    price_cny=price,
                    msrp_cny=msrp,
                    channel_url=f"https://item.jd.com/{sku}.html",
                )
            )
        return hits

    def get_goods_detail(self, sku_ids: list[str | int]) -> list[JdUnionHit]:
        ids = [int(s) for s in sku_ids if str(s).isdigit()]
        if not ids:
            return []
        biz = {"goodsReqDTO": {"skuIds": ids[:20], "sceneId": 1}}
        raw = self._request("jd.union.open.goods.detail.query", biz)
        result = self._unwrap("jd.union.open.goods.detail.query", raw)
        data = result.get("data") or result.get("goodsList") or []
        if isinstance(data, dict):
            data = data.get("goodsList") or data.get("list") or [data]
        hits: list[JdUnionHit] = []
        for item in data or []:
            sku = str(item.get("skuId") or item.get("sku_id") or "")
            if not sku:
                continue
            price, msrp = self._parse_prices(item)
            title = str(item.get("skuName") or item.get("title") or item.get("productName") or "")
            hits.append(
                JdUnionHit(
                    sku_id=sku,
                    title=title,
                    price_cny=price,
                    msrp_cny=msrp,
                    channel_url=f"https://item.jd.com/{sku}.html",
                )
            )
        return hits


def get_client() -> JdUnionClient | None:
    creds = _credentials()
    if not creds:
        return None
    return JdUnionClient(creds[0], creds[1])


def union_search(keyword: str) -> list[JdUnionHit]:
    client = get_client()
    if not client:
        return []
    try:
        return client.search_goods(keyword)
    except Exception:
        return []


def pick_best_union_hit(hits: list[JdUnionHit], brand: str, model: str) -> JdUnionHit | None:
    from sources.channel.jd_client import JdSearchHit, pick_best_hit

    if not hits:
        return None
    converted = [
        JdSearchHit(
            sku_id=h.sku_id,
            title=h.title,
            price_cny=h.price_cny,
            msrp_cny=h.msrp_cny,
            channel_url=h.channel_url,
            shop_hint=h.shop_hint,
        )
        for h in hits
    ]
    best = pick_best_hit(converted, brand, model)
    if not best:
        return None
    for h in hits:
        if h.sku_id == best.sku_id:
            return h
    return hits[0]


def union_detail(sku_id: str | int) -> JdUnionHit | None:
    client = get_client()
    if not client:
        return None
    try:
        hits = client.get_goods_detail([sku_id])
        return hits[0] if hits else None
    except Exception:
        return None
