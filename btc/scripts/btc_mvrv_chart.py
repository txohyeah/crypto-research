#!/usr/bin/env python3
"""BTC MVRV 趋势图

数据来源：本地 daily_metrics 表
"""

import sys
import sqlite3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent.parent / "reports"
DB_PATH = Path(__file__).parent.parent / "data" / "btc_monitor.db"


def fetch_mvrv_data(days: int = 90) -> list:
    """从本地数据库读取 MVRV 数据"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """SELECT date, mvrv, price_usd FROM daily_metrics 
           WHERE mvrv IS NOT NULL 
           ORDER BY date DESC LIMIT ?""",
        (days,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def render_chart(data: list, output_path: str = None) -> str:
    """渲染 MVRV 趋势图"""
    dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in data]
    mvrvs = [d["mvrv"] for d in data]

    fig, ax1 = plt.subplots(figsize=(12, 5))

    # MVRV 主线
    color1 = "#e74c3c"
    ax1.plot(dates, mvrvs, color=color1, linewidth=2, label="MVRV")
    ax1.set_xlabel("Date", fontsize=11)
    ax1.set_ylabel("MVRV", color=color1, fontsize=11)
    ax1.tick_params(axis="y", labelcolor=color1)

    # 关键阈值线
    ax1.axhline(y=1.0, color="#27ae60", linewidth=1.5, linestyle="--", alpha=0.7, label="Bottom (1.0)")
    ax1.axhline(y=1.2, color="#f39c12", linewidth=1.5, linestyle="--", alpha=0.7, label="Accumulate (1.2)")
    ax1.axhline(y=1.5, color="#e74c3c", linewidth=1.5, linestyle="--", alpha=0.7, label="Overheated (1.5)")

    # 标注最新值
    ax1.annotate(
        f"{mvrvs[-1]:.4f}",
        xy=(dates[-1], mvrvs[-1]),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=10,
        color=color1,
        fontweight="bold",
        arrowprops=dict(arrowstyle="->", color=color1),
    )

    # 均值线
    avg_mvrv = sum(mvrvs) / len(mvrvs)
    ax1.axhline(y=avg_mvrv, color="#3498db", linewidth=1, linestyle=":", alpha=0.5,
                label=f"Avg: {avg_mvrv:.4f}")

    # 格式化
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=9)

    fig.suptitle("BTC MVRV Ratio", fontsize=14, fontweight="bold")
    fig.tight_layout()

    if not output_path:
        output_path = str(OUTPUT_DIR / "mvrv_latest.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch_mvrv_data(90)
    if not data:
        print("❌ 无 MVRV 数据")
        return
    print(f"📊 读取到 {len(data)} 天数据 ({data[0]['date']} ~ {data[-1]['date']})")
    path = render_chart(data)
    print(f"✅ MVRV 趋势图已保存: {path}")


if __name__ == "__main__":
    main()
