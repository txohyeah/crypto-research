#!/usr/bin/env python3
"""BTC 永续合约资金费率趋势图

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

# ---------- 配置 ----------
OUTPUT_DIR = Path(__file__).parent.parent / "reports"
DB_PATH = Path(__file__).parent.parent / "data" / "btc_monitor.db"


def fetch_funding_data(days: int = 60) -> list:
    """从本地数据库读取资金费率数据"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """SELECT date, funding_rate FROM daily_metrics 
           WHERE funding_rate IS NOT NULL 
           ORDER BY date DESC LIMIT ?""",
        (days,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def render_chart(data: list, output_path: str = None) -> str:
    """渲染资金费率趋势图"""
    dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in data]
    rates = [d["funding_rate"] * 100 for d in data]  # 转为百分比

    fig, ax = plt.subplots(figsize=(12, 5))

    # 颜色：正=绿，负=红
    colors = ["#27ae60" if r > 0 else "#e74c3c" for r in rates]

    bars = ax.bar(dates, rates, color=colors, width=0.8, alpha=0.8)

    # 零线
    ax.axhline(y=0, color="#333", linewidth=0.8, linestyle="-")

    # 均值线
    avg_rate = sum(rates) / len(rates)
    ax.axhline(y=avg_rate, color="#3498db", linewidth=1.5, linestyle="--", alpha=0.7,
               label=f"Avg: {avg_rate:.4f}%")

    # 标注最新值
    ax.annotate(
        f"{rates[-1]:.4f}%",
        xy=(dates[-1], rates[-1]),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=10,
        color="#2c3e50",
        fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#2c3e50"),
    )

    # 统计正/负天数
    pos_days = sum(1 for r in rates if r > 0)
    neg_days = sum(1 for r in rates if r <= 0)
    stats_text = f"+{pos_days} / -{neg_days} days"
    ax.text(0.02, 0.95, stats_text, transform=ax.transAxes,
            fontsize=11, verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#ecf0f1", alpha=0.8))

    # 格式化
    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Funding Rate (%)", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)

    fig.suptitle("BTC Perpetual Futures - Funding Rate (8h Avg)", fontsize=14, fontweight="bold")
    fig.tight_layout()

    if not output_path:
        output_path = str(OUTPUT_DIR / "funding_rate_latest.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch_funding_data(60)
    if not data:
        print("❌ 无资金费率数据")
        return
    print(f"📊 读取到 {len(data)} 天数据 ({data[0]['date']} ~ {data[-1]['date']})")
    path = render_chart(data)
    print(f"✅ 资金费率趋势图已保存: {path}")


if __name__ == "__main__":
    main()
