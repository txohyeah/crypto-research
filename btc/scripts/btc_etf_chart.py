#!/usr/bin/env python3
"""BTC ETF 流入图表生成器（模板化）

用法:
    # 传入最近 N 天数据生成图表
    python3 btc_etf_chart.py --days 20

    # 传入自定义 JSON 数据
    python3 btc_etf_chart.py --data '[["2026-08-20", 606.3], ["2026-08-21", 307.5]]'

    # 指定输出路径
    python3 btc_etf_chart.py --days 20 --output /tmp/etf_chart.png
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from btc_monitor_db import get_recent_metrics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker


# ============================================================
# 样式常量（模板）
# ============================================================

COLORS = {
    'inflow': '#2ecc71',       # 流入 - 绿色
    'outflow': '#e74c3c',      # 流出 - 红色
    'inflow_text': '#27ae60',
    'outflow_text': '#c0392b',
    'zero_line': '#999999',
    'grid': '#e0e0e0',
    'window_bg': '#3498db',
    'title': '#2c3e50',
    'subtitle': '#7f8c8d',
    'stats': '#555555',
    'border': '#cccccc',
}

FIGURE_SIZE = (14, 6)
DPI = 150
BAR_WIDTH = 0.75
FONT_TITLE = 14
FONT_LABEL = 11
FONT_BAR = 9
FONT_STATS = 11


def load_data(days: int = 20) -> list[tuple[str, float]]:
    """从数据库加载最近 N 个交易日的 ETF 数据（排除周末）"""
    from datetime import datetime as dt
    rows = get_recent_metrics(days + 15)
    etf_data = []
    for r in rows:
        if r.get('etf_net_flow_m') is not None:
            d = dt.strptime(r['date'], "%Y-%m-%d")
            # 排除周六(5)、周日(6)
            if d.weekday() < 5:
                etf_data.append((r['date'], r['etf_net_flow_m']))
    # 按日期正序，取最近 N 个交易日
    etf_data.sort(key=lambda x: x[0])
    return etf_data[-days:]


def render_chart(data: list[tuple[str, float]] = None, output_path: str = None, days: int = 20) -> str:
    """渲染图表并保存，返回文件路径。data 为空时自动从数据库读取"""
    if data is None:
        data = load_data(days)
    if not data:
        print("❌ 无 ETF 数据")
        return None

    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in data]
    flows = [f for _, f in data]

    # 颜色
    colors = [COLORS['inflow'] if f >= 0 else COLORS['outflow'] for f in flows]

    # ---------- 画布 ----------
    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    fig.patch.set_facecolor('white')

    # ---------- 柱状图 ----------
    bars = ax.bar(dates, flows, color=colors, width=BAR_WIDTH,
                  edgecolor='white', linewidth=0.5, zorder=3)

    # 数值标签
    for bar, val in zip(bars, flows):
        y = bar.get_height()
        label = f'+{val:.0f}M' if val >= 0 else f'{val:.0f}M'
        color = COLORS['inflow_text'] if val >= 0 else COLORS['outflow_text']
        offset = 18 if y >= 0 else -22
        ax.text(bar.get_x() + bar.get_width() / 2., y + offset, label,
                ha='center', va='bottom' if y >= 0 else 'top',
                fontsize=FONT_BAR, fontweight='bold', color=color)

        # 柱内百分比（流入占比）
        if val >= 0:
            ax.text(bar.get_x() + bar.get_width() / 2., y / 2,
                    f'{val:.0f}', ha='center', va='center',
                    fontsize=7, color='white', alpha=0.8)

    # ---------- 零线 + 网格 ----------
    ax.axhline(y=0, color=COLORS['zero_line'], linestyle='--', linewidth=0.8, alpha=0.6)
    ax.grid(axis='y', color=COLORS['grid'], alpha=0.3, zorder=0)
    ax.set_axisbelow(True)

    # ---------- 坐标轴 ----------
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=8, maxticks=15))
    plt.xticks(rotation=45, ha='right', fontsize=9)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x:,.0f}'))
    ax.set_ylabel('Net Flow (Million USD)', fontsize=FONT_LABEL)

    # ---------- 边框美化 ----------
    for spine in ax.spines.values():
        spine.set_color(COLORS['border'])
    ax.tick_params(colors='#666666')

    # ---------- 标题 ----------
    ax.set_title('US Spot Bitcoin ETF — Daily Net Flows',
                 fontsize=FONT_TITLE, fontweight='bold', color=COLORS['title'], pad=18)

    # 副标题：日期范围
    date_range = f'{dates[0].strftime("%Y-%m-%d")}  →  {dates[-1].strftime("%Y-%m-%d")}'
    ax.text(0.5, 1.02, date_range, transform=ax.transAxes, ha='center',
            fontsize=10, color=COLORS['subtitle'], style='italic')

    # ---------- 20 日窗口高亮 ----------
    if len(dates) >= 15:
        x_min = mdates.date2num(dates[0]) - 0.6
        x_max = mdates.date2num(dates[-1]) + 0.6
        ax.axvspan(x_min, x_max, alpha=0.05, color=COLORS['window_bg'], zorder=0)

    # ---------- 统计信息 ----------
    inflow_days = sum(1 for f in flows if f > 0)
    outflow_days = sum(1 for f in flows if f < 0)
    total_net = sum(flows)
    avg = total_net / len(flows)
    max_in = max(flows)
    max_out = min(flows)

    stats = (
        f'Net: ${total_net/1000:.1f}B  |  '
        f'{inflow_days}in / {outflow_days}out  |  '
        f'Avg: ${avg:.0f}M/day  |  '
        f'Peak: +${max_in:.0f}M / ${max_out:.0f}M'
    )
    ax.text(0.5, -0.19, stats, transform=ax.transAxes, ha='center',
            fontsize=FONT_STATS, color=COLORS['stats'], style='italic')

    # ---------- 筑底信号标注 ----------
    signal = f'OK {inflow_days}/20 >=14' if inflow_days >= 14 else f'NO {inflow_days}/20 <14'
    signal_color = '#27ae60' if inflow_days >= 14 else '#c0392b'
    ax.text(0.98, 0.95, f'ETF Signal: {signal}', transform=ax.transAxes,
            ha='right', va='top', fontsize=10, fontweight='bold', color=signal_color,
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor=signal_color, alpha=0.9))

    plt.tight_layout()

    # 输出路径
    if not output_path:
        report_dir = Path(__file__).parent.parent / 'reports'
        report_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(report_dir / 'etf_flows_latest.png')

    fig.savefig(output_path, dpi=DPI, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f'✅ 图表已保存: {output_path}')
    return output_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='BTC ETF 流入图表')
    parser.add_argument('--days', type=int, default=20, help='显示最近 N 个交易日（默认 20）')
    parser.add_argument('--data', type=str, help='JSON 格式自定义数据 [[date, flow], ...]')
    parser.add_argument('--output', type=str, help='输出文件路径')
    args = parser.parse_args()

    if args.data:
        data = json.loads(args.data)
        data = [(d, float(f)) for d, f in data]
    else:
        data = load_data(args.days)

    render_chart(data, args.output)
