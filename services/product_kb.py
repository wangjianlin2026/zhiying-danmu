from __future__ import annotations

import json
import random
import threading
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
KB_PATH = BASE_DIR / "data" / "product_kb.json"

_lock = threading.RLock()
_cache = {"mtime": 0.0, "data": None}


def _load() -> dict:
    """热加载产品知识库：文件一改，下次提问立即生效。"""
    try:
        mtime = KB_PATH.stat().st_mtime
    except OSError:
        return {"enabled": True, "products": []}
    with _lock:
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
        try:
            with KB_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {"enabled": True, "products": []}
        if not isinstance(data, dict):
            data = {"enabled": True, "products": []}
        _cache["mtime"] = mtime
        _cache["data"] = data
        return data


def products() -> list[dict]:
    data = _load()
    if not data.get("enabled", True):
        return []
    return [item for item in (data.get("products") or []) if isinstance(item, dict)]


def match(text: str) -> dict | None:
    """按产品名/别名命中，供弹幕提问时定位到具体产品。"""
    haystack = str(text or "").strip().lower()
    if not haystack:
        return None
    for product in products():
        names = [product.get("name", "")] + list(product.get("aliases") or [])
        for name in names:
            needle = str(name or "").strip().lower()
            if needle and needle in haystack:
                return product
    return None


FAQ_KEYS = (
    "多少钱", "价格", "怎么用", "怎么使用", "适合", "规格",
    "售后", "发货", "库存", "效果", "材质", "区别",
)


def answer_for(text: str, limit: int = 28) -> str:
    """返回可直接朗读的单句答案；小智 detect 通道只吃短句。"""
    product = match(text)
    if product is None:
        return ""
    question = str(text or "")
    faq = product.get("faq") or {}
    answer = ""
    for key, value in faq.items():
        if key and str(key) in question and value:
            answer = str(value)
            break
    if not answer:
        for key in FAQ_KEYS:
            if key in question and faq.get(key):
                answer = str(faq[key])
                break
    if not answer:
        answer = str(product.get("short_pitch") or "")
    answer = " ".join(answer.split())
    return answer[:limit]


def random_pitch(limit: int = 30) -> str:
    """给话题轮播提供一条产品卖点短句。"""
    items = products()
    if not items:
        return ""
    product = random.choice(items)
    points = [
        str(point).strip()
        for point in (product.get("selling_points") or [])
        if str(point).strip()
    ]
    base = random.choice(points) if points else str(product.get("short_pitch") or "")
    name = str(product.get("name") or "").strip()
    text = base if not name or name in base else f"{name}{base}"
    return text[:limit]
