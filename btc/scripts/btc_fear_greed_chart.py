#!/usr/bin/env python3
"""BTC 恐惧贪婪指数趋势图

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


def fetch_fg_data(days: int = 90) -> list:
    """从本地数据库读取恐惧贪婪数据"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """SELECT date, fear_greed_value, fear_greed_label FROM daily_metrics 
           WHERE fear_greed_value IS NOT NULL 
           ORDER BY date DESC LIMIT ?""",
        (days,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def get_color(val):
    """根据值返回颜色"""
    if val < 15:
        return "#8B0000"  # 极度恐惧（深红）
    elif val < 25:
        return "#e74c3c"  # 恐惧（红）
    elif val < 45:
        return "#f39c12"  # 中性偏恐惧（橙）
    elif val < 55:
        return "#f1c40f"  # 中性（黄）
    elif val < 75:
        return "#27ae60"  # 贪婪（绿）
    else:
        return "#2ecc71"  # 极度贪婪（亮绿）


def render_chart(data: list, output_path: str = None) -> str:
    """渲染恐惧贪婪趋势图"""
    dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in data]
    values = [d["fear_greed_value"] for d in data]

    fig, ax = plt.subplots(figsize=(12, 5))

    # 背景区域
    ax.axhspan(0, 15, alpha=0.15, color="#8B0000", label="Extreme Fear")
    ax.axhspan(15, 25, alpha=0.1, color="#e74c3c")
    ax.axhspan(25, 55, alpha=0.1, color="#f39c12", label="Neutral")
    ax.axhspan(55, 75, alpha=0.1, color="#27ae60")
    ax.axhspan(75, 100, alpha=0.15, color="#2ecc71", label="Extreme Greed")

    # 柱状图
    colors = [get_color(v) for v in values]
    ax.bar(dates, values, color=colors, width=0.8, alpha=0.85)

    # 关键阈值线
    ax.axhline(y=15, color="#8B0000", linewidth=1, linestyle="--", alpha=0.6)
    ax.axhline(y=25, color="#e74c3c", linewidth=1, linestyle="--", alpha=0.6)
    ax.axhline(y=55, color="#27ae60", linewidth=1, linestyle="--", alpha=0.6)
    ax.axhline(y=75, color="#2ecc71", linewidth=1, linestyle="--", alpha=0.6)

    # 标注最新值
    ax.annotate(
        f"{values[-1]}",
        xy=(dates[-1], values[-1]),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=11,
        fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#2c3e50"),
    )

    # 统计
    extreme_fear = sum(1 for v in values if v < 15)
    fear = sum(1 for v in values if 15 <= v < 25)
    neutral = sum(1 for v in values if 25 <= v < 55)
    greed = sum(1 for v in values if 55 <= v < 75)
    extreme_greed = sum(1 for v in values if v >= 75)
    stats = f"Fear:{extreme_fear+fear} | Neutral:{neutral} | Greed:{greed+extreme_greed}"
    ax.text(0.02, 0.95, stats, transform=ax.transAxes,
            fontsize=10, verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#ecf0f1", alpha=0.8))

    # 格式化
    ax.set_ylim(0, 100)
    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Fear & Greed Index", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax.grid(True, axis="y", alpha=0.3)

    fig.suptitle("BTC Fear & Greed Index", fontsize=14, fontweight="bold")
    fig.tight_layout()

    if not output_path:
        output_path = str(OUTPUT_DIR / "fear_greed_latest.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch_fg_data(90)
    if not data:
        print("❌ 无恐惧贪婪数据")
        return
    print(f"📊 读取到 {len(data)} 天数据 ({data[0]['date']} ~ {data[-1]['date']})")
    path = render_chart(data)
    print(f"✅ 恐惧贪婪趋势图已保存: {path}")


if __name__ == "__main__":
    main()
