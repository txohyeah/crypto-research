#!/usr/bin/env python3
"""回填 Binance BTCUSDT 历史资金费率到 daily_metrics 表

逻辑：
1. 调用 Binance /fapi/v1/fundingRate，一次拉 1000 条（约 333 天）
2. 按日期分组，计算每日均值写入 daily_metrics.funding_rate
"""

import sys
import sqlite3
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROXY = {"https": "http://127.0.0.1:7890"}
DB_PATH = Path(__file__).parent.parent / "data" / "btc_monitor.db"
EAST8 = timezone(timedelta(hours=8))


def fetch_all_funding_rates() -> list:
    """拉取 Binance 历史资金费率"""
    print("📡 从 Binance 拉取历史资金费率...")
    r = requests.get(
        "https://fapi.binance.com/fapi/v1/fundingRate",
        params={"symbol": "BTCUSDT", "limit": 1000},
        proxies=PROXY,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    print(f"   获取到 {len(data)} 条记录")
    return data


def aggregate_daily(data: list) -> dict:
    """按日期（东八区）分组，计算每日均值"""
    daily = {}
    for item in data:
        ts_ms = item["fundingTime"]
        rate = float(item["fundingRate"])
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=EAST8)
        date_str = dt.strftime("%Y-%m-%d")
        if date_str not in daily:
            daily[date_str] = []
        daily[date_str].append(rate)

    # 计算均值
    result = {}
    for date, rates in daily.items():
        result[date] = sum(rates) / len(rates)
    return result


def update_db(daily: dict):
    """写入数据库"""
    conn = sqlite3.connect(str(DB_PATH))
    count = 0
    for date, avg_rate in sorted(daily.items()):
        conn.execute(
            """INSERT INTO daily_metrics (date, funding_rate) VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET funding_rate = excluded.funding_rate""",
            (date, avg_rate),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def main():
    data = fetch_all_funding_rates()
    daily = aggregate_daily(data)
    print(f"📊 聚合为 {len(daily)} 天数据")

    # 显示日期范围
    dates = sorted(daily.keys())
    print(f"   范围: {dates[0]} ~ {dates[-1]}")

    count = update_db(daily)
    print(f"✅ 写入 {count} 天到数据库")

    # 验证
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        "SELECT COUNT(*) FROM daily_metrics WHERE funding_rate IS NOT NULL"
    )
    total = cur.fetchone()[0]
    conn.close()
    print(f"📈 数据库中共 {total} 天资金费率数据")


if __name__ == "__main__":
    main()
