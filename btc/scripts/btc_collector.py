#!/usr/bin/env python3
"""BTC 信号监控 - 数据采集脚本

数据源:
1. Binance 现货日K - 价格、现货成交量（免费，走代理）
2. bitcoin-data.com - MVRV（免费无key，直连，T+1 出数）
3. alternative.me - 恐惧贪婪指数（免费）
4. Binance API - 资金费率、未平仓量（免费，走代理）
5. Tavily/CoinMarketCap - ETF 资金流（需 Agent 调用）

注: CoinMetrics Community API 已于 2026-08-23 退役（间歇性403、链上量从未有效），
    collect_coinmetrics 保留仅作备用。
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta

# 添加脚本目录到路径
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from btc_monitor_db import init_db, upsert_daily_metrics, log_collect, get_recent_metrics

# 代理配置（Clash 默认端口 7890）
HTTP_PROXY = os.environ.get("HTTP_PROXY", "http://127.0.0.1:7890")
HTTPS_PROXY = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7890")

# 需要代理的域名
PROXY_DOMAINS = ["binance.com", "fapi.binance.com"]

# 见顶系统 v2：bitcoin-data.com 首次回填窗口（天）；免费档限流 10 req/hour
TOP_WINDOW_DAYS = 400


def fetch_json(url: str, timeout: int = 15, use_proxy: bool = None) -> dict | list | None:
    """通用 JSON 请求，自动判断是否需要代理"""
    try:
        # 判断是否需要代理
        if use_proxy is None:
            use_proxy = any(domain in url for domain in PROXY_DOMAINS)

        if use_proxy:
            proxy_handler = urllib.request.ProxyHandler({
                "http": HTTP_PROXY,
                "https": HTTPS_PROXY,
            })
            opener = urllib.request.build_opener(proxy_handler)
        else:
            opener = urllib.request.build_opener()

        req = urllib.request.Request(url, headers={"User-Agent": "BTC-Monitor/1.0"})
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 数据源 1: CoinMetrics Community API（已弃用，仅留备用）
# ============================================================

def collect_coinmetrics(target_date: str = None) -> dict:
    """[已弃用 2026-08-23] CoinMetrics 社区API间歇性403、链上量从未有效。

    替代方案：价格+现货量 → collect_binance_spot_klines；MVRV → collect_mvrv_bitcoindata。
    本函数保留不删，仅在需要回溯对比时手动调用。
    """
    if not target_date:
        target_date = date.today().isoformat()

    start = target_date
    end = target_date

    # 增加 TxTfrValAdjNtv（链上交易量，USD 计价）
    url = (
        f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        f"?assets=btc"
        f"&metrics=CapMVRVCur,PriceUSD,FlowInExNtv,FlowOutExNtv,TxTfrValAdjNtv"
        f"&frequency=1d"
        f"&start_time={start}"
        f"&end_time={end}"
    )

    data = fetch_json(url)
    if "error" in data:
        return {"source": "coinmetrics", "status": "error", "message": data["error"]}

    records = data.get("data", [])
    if not records:
        return {"source": "coinmetrics", "status": "no_data", "message": f"无 {target_date} 数据"}

    rec = records[0]

    def safe_float(val, default=None):
        """安全转换为 float，None 返回 default"""
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    result = {
        "date": target_date,
        "price_usd": safe_float(rec.get("PriceUSD")),
        "mvrv": safe_float(rec.get("CapMVRVCur")),
        "exchange_flow_in": safe_float(rec.get("FlowInExNtv")),
        "exchange_flow_out": safe_float(rec.get("FlowOutExNtv")),
        "spot_volume": safe_float(rec.get("TxTfrValAdjNtv")),  # 链上交易量（USD）
    }

    return {"source": "coinmetrics", "status": "ok", "data": result}


# ============================================================
# 数据源 2: alternative.me（恐惧贪婪指数）
# ============================================================

def collect_fear_greed(target_date: str = None) -> dict:
    """从 alternative.me 获取恐惧贪婪指数"""
    url = "https://api.alternative.me/fng/?limit=1&format=json"

    data = fetch_json(url)
    if "error" in data:
        return {"source": "fear_greed", "status": "error", "message": data["error"]}

    records = data.get("data", [])
    if not records:
        return {"source": "fear_greed", "status": "no_data", "message": "无数据"}

    rec = records[0]
    # API 返回的是最新数据，日期可能是今天或昨天
    api_date = datetime.fromtimestamp(int(rec.get("timestamp", 0))).strftime("%Y-%m-%d")

    result = {
        "fear_greed_value": int(rec.get("value", 0)),
        "fear_greed_label": rec.get("value_classification", ""),
    }

    return {"source": "fear_greed", "status": "ok", "date": api_date, "data": result}


# ============================================================
# 数据源 3: Binance API（资金费率 + 未平仓量）
# ============================================================

def collect_binance() -> dict:
    """从 Binance 获取资金费率和未平仓量"""
    base = "https://fapi.binance.com"

    # 资金费率（最新）
    fr_url = f"{base}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1"
    fr_data = fetch_json(fr_url)
    if isinstance(fr_data, dict) and "error" in fr_data:
        return {"source": "binance", "status": "error", "message": fr_data["error"]}

    funding_rate = float(fr_data[0]["fundingRate"]) if fr_data else None

    # 未平仓量
    oi_url = f"{base}/fapi/v1/openInterest?symbol=BTCUSDT"
    oi_data = fetch_json(oi_url)
    if isinstance(oi_data, dict) and "error" in oi_data:
        return {"source": "binance", "status": "error", "message": oi_data["error"]}

    open_interest = float(oi_data.get("openInterest", 0)) if oi_data else None

    result = {
        "funding_rate": funding_rate,
        "open_interest": open_interest,
    }

    return {"source": "binance", "status": "ok", "data": result}


def collect_binance_spot_klines(days: int = 400) -> dict:
    """
    从 Binance 现货日K获取 BTC 收盘价 + 成交量（USDT 计价 quoteVolume）

    一次请求同时产出 price_usd 与 spot_volume。只取已收盘完整日K —— 当日半根K线
    不写入历史行，避免凌晨采集时把接近 0 的量写入当天污染缩量判定（假缩量）、
    以及把盘中价冒充收盘价。

    另返回 latest = 当日实时价（未收盘K线最新价）：写入"今天"行供二次探底等
    "当前价"类判定使用；次日 cron 会将其修正为真实收盘价。

    返回 {"status": "ok", "count", "latest", "data": [{"date","price_usd","spot_volume"}, ...]}
    """
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit={days}"
    data = fetch_json(url)
    if isinstance(data, dict) and "error" in data:
        return {"source": "binance_spot_klines", "status": "error", "message": data["error"]}

    now_ms = datetime.now().timestamp() * 1000
    out, latest = [], None
    for k in data or []:
        open_ms, close_ms = k[0], k[6]
        d = datetime.fromtimestamp(open_ms / 1000).strftime("%Y-%m-%d")
        if close_ms >= now_ms:
            latest = {"date": d, "price_usd": float(k[4])}  # 当日实时价
            continue
        out.append({"date": d,
                    "price_usd": float(k[4]),     # UTC 日收盘价
                    "spot_volume": float(k[7])})  # quote asset volume (USDT)

    if not out:
        return {"source": "binance_spot_klines", "status": "no_data", "message": "无已收盘K线"}
    return {"source": "binance_spot_klines", "status": "ok",
            "count": len(out), "latest": latest, "data": out}


def collect_mvrv_bitcoindata(days: int = 400) -> dict:
    """
    从 bitcoin-data.com 获取 MVRV（免费无 key，替代 CoinMetrics CapMVRVCur）

    GET /api/v1/mvrv?startday=YYYY-MM-DD → [{"d","unixTs","mvrv"}, ...]
    数据 T+1 出数（今日请求最新为昨日值）；check_checklist 的 as_of/stale 兜底已兼容。
    直连无需代理。

    返回 {"status": "ok", "count", "data": [{"date", "mvrv"}, ...]}
    """
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    url = f"https://bitcoin-data.com/api/v1/mvrv?startday={start}"
    data = fetch_json(url)
    if isinstance(data, dict) and "error" in data:
        return {"source": "bitcoindata_mvrv", "status": "error", "message": data["error"]}
    if not data:
        return {"source": "bitcoindata_mvrv", "status": "no_data", "message": "空响应"}

    out = [{"date": r["d"], "mvrv": float(r["mvrv"])} for r in data if r.get("mvrv") is not None]
    if not out:
        return {"source": "bitcoindata_mvrv", "status": "no_data", "message": "无有效MVRV"}
    return {"source": "bitcoindata_mvrv", "status": "ok",
            "count": len(out), "data": out}


# ============================================================
# 数据源 3.5: bitcoin-data.com 见顶系统 v2 链上指标（六端点）
#   免费档限流 10 请求/小时 → 逐个请求间隔 sleep、遇 429 全局熔断
#   字段名自适应：端点返回 [{"d","unixTs","<field>"}]，自动探测数值型字段
#   增量拉取：从本地已有最新值往前 3 天缓冲开始，无历史才全量回填
# ============================================================

TOP_INDICATORS = [
    # (bitcoin-data.com endpoint, daily_metrics 列名)
    ("mvrv-zscore",     "mvrv_z"),
    ("sopr",            "sopr"),
    ("puell-multiple",  "puell"),
    ("nupl",            "nupl"),
    ("reserve-risk",    "reserve_risk"),
    ("realized-profit", "realized_profit"),
]


def _local_latest_date(column: str) -> str | None:
    """查某链上指标在本地库的最新日期"""
    from btc_monitor_db import get_conn
    conn = get_conn()
    row = conn.execute(
        f"SELECT MAX(date) AS d FROM daily_metrics WHERE {column} IS NOT NULL"
    ).fetchone()
    conn.close()
    return row["d"] if row and row["d"] else None


def collect_bitcoindata_indicator(endpoint: str, days: int = TOP_WINDOW_DAYS) -> dict:
    """
    从 bitcoin-data.com 拉取单个链上指标端点

    GET /api/v1/{endpoint}?startday=YYYY-MM-DD → [{"d","unixTs","<field>"}, ...]
    返回 {"status": "ok"/"no_data"/"error"/"rate_limited", "count",
          "data": [{"date", "value"}, ...], "field": 探测到的字段名}
    """
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    url = f"https://bitcoin-data.com/api/v1/{endpoint}?startday={start}"
    data = fetch_json(url)
    if isinstance(data, dict) and "error" in data:
        msg = str(data["error"])
        if "429" in msg or "RATE_LIMIT" in msg.upper():
            return {"source": f"bitcoindata_{endpoint}", "status": "rate_limited", "message": msg}
        return {"source": f"bitcoindata_{endpoint}", "status": "error", "message": msg}
    if not isinstance(data, list) or not data:
        return {"source": f"bitcoindata_{endpoint}", "status": "no_data", "message": "空响应"}

    # 字段自适应探测：排除日期/时间戳键，找第一个数值型字段
    skip_keys = {"d", "unixTs", "date", "t", "timestamp"}
    field = None
    for rec in data[:5]:
        for k, v in rec.items():
            if k in skip_keys:
                continue
            try:
                float(v)
                field = k
                break
            except (TypeError, ValueError):
                continue
        if field:
            break
    if not field:
        return {"source": f"bitcoindata_{endpoint}", "status": "no_data",
                "message": f"未找到数值字段，keys={list(data[0].keys())}"}

    out = []
    for r in data:
        if r.get(field) is not None:
            try:
                out.append({"date": r["d"], "value": float(r[field])})
            except (TypeError, ValueError):
                continue
    if not out:
        return {"source": f"bitcoindata_{endpoint}", "status": "no_data",
                "message": "无有效数值"}
    return {"source": f"bitcoindata_{endpoint}", "status": "ok",
            "count": len(out), "field": field, "data": out}


def collect_all_top_indicators(interval_sec: float = 8.0, verbose: bool = True) -> dict:
    """
    批量拉取六个见顶指标端点并写入 daily_metrics 对应列

    限流策略（免费档 10 请求/小时）：
      - 每次请求间隔 interval_sec 秒
      - 一旦 rate_limited 立即中止剩余端点（同窗口继续打必然再失败）
      - 增量拉取：已有数据的端点只补最近 ~10 天（3 天缓冲 × 幂等 upsert）
    返回 {"ok": [...], "failed": [...], "rate_limited": bool}
    """
    import time as _time
    summary = {"ok": [], "failed": [], "rate_limited": False}

    for i, (endpoint, column) in enumerate(TOP_INDICATORS):
        if i > 0:
            _time.sleep(interval_sec)

        latest = _local_latest_date(column)
        if latest:
            days = max(10, (date.today() - date.fromisoformat(latest)).days + 3 + 1)
        else:
            days = TOP_WINDOW_DAYS  # 首次全量回填

        r = collect_bitcoindata_indicator(endpoint, days=days)
        if r["status"] == "ok":
            from btc_monitor_db import upsert_daily_metrics
            for row in r["data"]:
                upsert_daily_metrics({"date": row["date"], column: row["value"]})
            summary["ok"].append((endpoint, column, r["count"]))
            if verbose:
                lv = r["data"][-1]
                print(f"    ✅ {endpoint:<16} → {column:<15} 覆盖{r['count']:>3}天 "
                      f"(字段'{r['field']}' 最新 {lv['date']}={lv['value']:.5g})")
        elif r["status"] == "rate_limited":
            summary["failed"].append((endpoint, column, "rate_limited"))
            summary["rate_limited"] = True
            if verbose:
                print(f"    ⏳ {endpoint} 触发小时限流，本轮跳过（明日 cron 自动续传）")
            break  # 同一小时配额已耗尽，后续必失败
        else:
            summary["failed"].append((endpoint, column, r.get("message", "")[:80]))
            if verbose:
                print(f"    ❌ {endpoint}: {r.get('message', '')[:80]}")

    return summary




def collect_etf_from_input(etf_net_flow_m: float = None, etf_total_aum_b: float = None) -> dict:
    """ETF 数据需要从 Tavily/Agent 获取，这里接收外部传入的值"""
    result = {
        "etf_net_flow_m": etf_net_flow_m,
        "etf_total_aum_b": etf_total_aum_b,
    }
    return {"source": "etf", "status": "ok", "data": result}


# ============================================================
# 主采集流程
# ============================================================

def collect_all(target_date: str = None, etf_net_flow_m: float = None, etf_total_aum_b: float = None) -> dict:
    """执行完整采集并写入数据库"""
    if not target_date:
        target_date = date.today().isoformat()

    print(f"🔄 开始采集 {target_date} 数据...")

    # 初始化数据库
    init_db()

    # 合并数据
    metrics = {"date": target_date}
    logs = []

    # 1. Binance 现货日K（价格+成交量，CoinMetrics 已退役）
    print("  📡 Binance 现货K线...")
    r1 = collect_binance_spot_klines()
    logs.append({"source": "binance_spot_klines", "status": r1["status"], "message": r1.get("message", "")})
    if r1["status"] == "ok":
        for row in r1["data"]:
            upsert_daily_metrics({"date": row["date"],
                                  "price_usd": row["price_usd"],
                                  "spot_volume": row["spot_volume"]})
        if r1.get("latest"):
            metrics.update({"price_usd": r1["latest"]["price_usd"]})
        lv = r1["data"][-1]
        print(f"    ✅ 覆盖 {r1['count']} 天, 最新收盘=${lv['price_usd']:,.2f}")

    # 1.6 MVRV (bitcoin-data.com)
    print("  📡 MVRV (bitcoin-data.com)...")
    r16 = collect_mvrv_bitcoindata()
    logs.append({"source": "bitcoindata_mvrv", "status": r16["status"], "message": r16.get("message", "")})
    if r16["status"] == "ok":
        for row in r16["data"]:
            upsert_daily_metrics({"date": row["date"], "mvrv": row["mvrv"]})
        mv = r16["data"][-1]
        print(f"    ✅ 覆盖 {r16['count']} 天, MVRV={mv['mvrv']:.4f} ({mv['date']})")
    else:
        print(f"    ❌ {r16.get('message')}")

    # 2. 恐惧贪婪
    print("  📡 Fear & Greed Index...")
    r2 = collect_fear_greed(target_date)
    logs.append(r2)
    if r2["status"] == "ok":
        metrics.update(r2["data"])
        print(f"    ✅ 恐惧贪婪={r2['data']['fear_greed_value']} ({r2['data']['fear_greed_label']})")
    else:
        print(f"    ❌ {r2['message']}")

    # 3. Binance
    print("  📡 Binance...")
    r3 = collect_binance()
    logs.append(r3)
    if r3["status"] == "ok":
        metrics.update(r3["data"])
        fr = r3['data']['funding_rate']
        oi = r3['data']['open_interest']
        print(f"    ✅ 资金费率={fr:.6f}, 未平仓={oi:.2f} BTC" if fr and oi else f"    ⚠️ 部分数据缺失")
    else:
        print(f"    ❌ {r3['message']}")

    # 4. ETF（外部传入）
    if etf_net_flow_m is not None or etf_total_aum_b is not None:
        print("  📡 ETF 数据（外部传入）...")
        r4 = collect_etf_from_input(etf_net_flow_m, etf_total_aum_b)
        metrics.update(r4["data"])
        print(f"    ✅ 净流入=${etf_net_flow_m}M, AUM=${etf_total_aum_b}B")
    else:
        print("  ⏭️  ETF 数据未传入（需通过 Tavily 获取）")
        metrics["etf_net_flow_m"] = None
        metrics["etf_total_aum_b"] = None

    # 写入数据库
    print("\n💾 写入数据库...")
    try:
        upsert_daily_metrics(metrics)
        print(f"    ✅ daily_metrics 已更新")
    except Exception as e:
        print(f"    ❌ 写入失败: {e}")

    # 记录采集日志
    for log in logs:
        log_collect(log["source"], log["status"], log.get("message", ""))

    print(f"\n✅ 采集完成: {target_date}")
    return metrics


def print_recent_summary(days: int = 7):
    """打印最近 N 天的数据摘要"""
    rows = get_recent_metrics(days)
    if not rows:
        print("无数据")
        return

    print(f"\n📊 最近 {days} 天数据:")
    print(f"{'日期':<12} {'价格':>10} {'MVRV':>8} {'恐惧贪婪':>8} {'ETF净流入':>12} {'资金费率':>10}")
    print("-" * 70)
    for r in rows:
        price = f"${r['price_usd']:,.0f}" if r.get('price_usd') else "-"
        mvrv = f"{r['mvrv']:.4f}" if r.get('mvrv') else "-"
        fg = str(r['fear_greed_value']) if r.get('fear_greed_value') is not None else "-"
        etf = f"${r['etf_net_flow_m']:.1f}M" if r.get('etf_net_flow_m') is not None else "-"
        fr = f"{r['funding_rate']:.6f}" if r.get('funding_rate') is not None else "-"
        print(f"{r['date']:<12} {price:>10} {mvrv:>8} {fg:>8} {etf:>12} {fr:>10}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BTC 信号监控数据采集")
    parser.add_argument("--date", help="采集日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--etf-flow", type=float, help="ETF 单日净流入（百万美元）")
    parser.add_argument("--etf-aum", type=float, help="ETF 总资产净值（十亿美元）")
    parser.add_argument("--summary", type=int, nargs="?", const=7, help="显示最近 N 天摘要")
    parser.add_argument("--init", action="store_true", help="仅初始化数据库")

    args = parser.parse_args()

    if args.init:
        init_db()
    elif args.summary is not None:
        print_recent_summary(args.summary)
    else:
        collect_all(
            target_date=args.date,
            etf_net_flow_m=args.etf_flow,
            etf_total_aum_b=args.etf_aum
        )
