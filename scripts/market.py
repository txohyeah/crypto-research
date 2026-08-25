#!/usr/bin/env python3
"""行情快照：BTC/ETH 价格(24h涨跌) + 恐惧贪婪指数。

输出单行 JSON 到 stdout，字段缺失时值为 null。
用法：python3 scripts/market.py
"""
import json
import os
import sys

import requests

PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:7890")


def get(url: str, timeout: int = 15):
    """直连优先，失败走代理。"""
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        proxies = {"http": PROXY_URL, "https": PROXY_URL}
        r = requests.get(url, timeout=timeout, proxies=proxies)
        r.raise_for_status()
        return r.json()


def fmt(price, chg):
    if price is None:
        return None
    sign = "+" if (chg or 0) >= 0 else ""
    return f"${price:,.0f} ({sign}{chg:.1f}%)" if chg is not None else f"${price:,.0f}"


def main() -> None:
    out = {"btc": None, "eth": None, "fng": None}
    try:
        px = get("https://api.coingecko.com/api/v3/simple/price"
                 "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")
        out["btc"] = fmt(px["bitcoin"].get("usd"), px["bitcoin"].get("usd_24h_change"))
        out["eth"] = fmt(px["ethereum"].get("usd"), px["ethereum"].get("usd_24h_change"))
    except Exception:
        pass
    try:
        fng = get("https://api.alternative.me/fng/?limit=1")
        d = fng["data"][0]
        out["fng"] = f"{d['value']} {d['value_classification']}"
    except Exception:
        pass
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
