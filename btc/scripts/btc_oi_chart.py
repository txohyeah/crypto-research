#!/usr/bin/env python3
"""BTC 永续合约 OI 趋势图

数据来源：本地 oi_daily 表（由 btc_daily_collect.py 每日同步）
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
SYMBOL = "BTCUSDT"


def fetch_oi_data(days: int = 180) -> list:
    """从本地数据库读取 OI 数据"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT date, oi_btc, oi_usd FROM oi_daily ORDER BY date DESC LIMIT ?",
        (days,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows.reverse()  # 按日期正序
    return rows


def render_chart(data: list, output_path: str = None) -> str:
    """渲染 OI 趋势图，返回文件路径"""
    dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in data]
    oi_values = [d["oi_btc"] for d in data]
    oi_usd = [d["oi_usd"] / 1e9 for d in data]  # 单位: B USD

    fig, ax1 = plt.subplots(figsize=(12, 5))

    # OI (BTC)
    color1 = "#2196F3"
    ax1.plot(dates, oi_values, color=color1, linewidth=2, label="OI (BTC)")
    ax1.set_xlabel("Date", fontsize=11)
    ax1.set_ylabel("Open Interest (BTC)", color=color1, fontsize=11)
    ax1.tick_params(axis="y", labelcolor=color1)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    # Y 轴范围自适应，留 10% 边距
    oi_min, oi_max = min(oi_values), max(oi_values)
    margin = (oi_max - oi_min) * 0.1 or 1000
    ax1.set_ylim(oi_min - margin, oi_max + margin)

    # OI Value (USD) - 右轴
    ax2 = ax1.twinx()
    color2 = "#FF9800"
    ax2.plot(dates, oi_usd, color=color2, linewidth=2, linestyle="--", label="OI Value (USD)")
    ax2.set_ylabel("OI Value ($ Billion)", color=color2, fontsize=11)
    ax2.tick_params(axis="y", labelcolor=color2)
    # 右轴范围自适应
    val_min, val_max = min(oi_usd), max(oi_usd)
    val_margin = (val_max - val_min) * 0.1 or 0.1
    ax2.set_ylim(val_min - val_margin, val_max + val_margin)

    # 均值线
    avg_oi = sum(oi_values) / len(oi_values)
    ax1.axhline(y=avg_oi, color=color1, linestyle=":", alpha=0.5, label=f"Avg: {avg_oi:,.0f} BTC")

    # 标注最新值
    ax1.annotate(
        f"{oi_values[-1]:,.0f}",
        xy=(dates[-1], oi_values[-1]),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=10,
        color=color1,
        fontweight="bold",
        arrowprops=dict(arrowstyle="->", color=color1),
    )

    # 格式化
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax1.grid(True, alpha=0.3)

    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    fig.suptitle(f"BTC Perpetual Futures - Open Interest ({SYMBOL})", fontsize=14, fontweight="bold")
    fig.tight_layout()

    if not output_path:
        output_path = str(OUTPUT_DIR / "oi_trend_latest.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch_oi_data(180)  # 最多取180天
    if not data:
        print("❌ 无 OI 数据")
        return
    print(f"📊 读取到 {len(data)} 天 OI 数据 ({data[0]['date']} ~ {data[-1]['date']})")
    path = render_chart(data)
    print(f"✅ OI 趋势图已保存: {path}")


if __name__ == "__main__":
    main()
