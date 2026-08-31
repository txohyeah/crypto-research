#!/usr/bin/env python3
"""BTC 每日报告 HTML 生成器

使用 Jinja2 模板渲染报告，数据与展示分离。
"""

import sys
import base64
from pathlib import Path
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).parent))
from btc_monitor_db import get_recent_metrics, check_checklist, check_top_checklist

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
OUTPUT_DIR = Path(__file__).parent.parent / "reports"


def load_chart_b64(path: str) -> str:
    """读取图片文件并转为 base64"""
    if path and Path(path).exists():
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""


def get_latest_metrics(recent: list) -> dict:
    """从多天数据中取每个指标的最新可用值"""
    metrics = {}
    fields = ['price_usd', 'mvrv', 'fear_greed_value', 'fear_greed_label',
              'funding_rate', 'open_interest', 'etf_net_flow_m', 'etf_total_aum_b',
              'exchange_flow_in', 'exchange_flow_out']
    for f in fields:
        for r in recent:
            if r.get(f) is not None:
                metrics[f] = r[f]
                break
    return metrics


def _second_test_display(checklist: dict) -> str:
    """二次探底信号值展示：动态前低 + 来源 + 锚定日期"""
    d = checklist.get('details', {}).get('5_second_test', {})
    if not d.get('support_level'):
        return '-'
    src = {'manual': '人工', 'auto': '自动'}.get(d.get('support_source'), d.get('support_source', '?'))
    return f"前低 ${d['support_level']:,.0f} ({src}·锚定{d.get('support_anchor', '-')})"


def build_template_data(target_date, chart_paths: dict) -> dict:
    """构建模板渲染所需的全部数据"""
    checklist = check_checklist()
    recent = get_recent_metrics(7)
    latest = get_latest_metrics(recent)

    # ---- 筑底清单 ----
    signal_map = [
        ('1_mvrv',       'MVRV 回落 1.0-1.2',  lambda: f'{latest["mvrv"]:.4f}' if latest.get('mvrv') else '-', '1.0-1.2'),
        ('2_fear_greed', '恐惧贪婪 <15',        lambda: str(latest.get('fear_greed_value') or '-'), '<15'),
        ('3_etf_inflow', 'ETF 流入 ≥14/20 天',   lambda: f'{checklist["details"].get("3_etf_inflow", {}).get("inflow_days", 0)}/20', '≥14/20'),
        ('4_spot_perp',  '现货需求 30天累计+7日均值',   lambda: f'累计 {checklist["details"].get("4_spot_perp", {}).get("cumulative_30d", 0):.4f} / 7日均值 {checklist["details"].get("4_spot_perp", {}).get("avg_7d", 0):.6f}', '>0 且 >0'),
        ('5_second_test','二次探底不破低',         lambda: _second_test_display(checklist), '回踩不破前低'),
        ('6_time_window','时间窗口 Q4',           lambda: '2026-08', '2026-Q4'),
    ]

    signals = []
    for key, name, value_fn, target in signal_map:
        d = checklist.get('details', {}).get(key, {})
        ok = d.get('status', False)
        signals.append({
            'icon': '✅' if ok else '❌',
            'name': name,
            'value': value_fn(),
            'target': target,
            'color': '#27ae60' if ok else '#e74c3c',
        })

    # ---- 图表列表 ----
    chart_defs = [
        ('📈', 'MVRV 趋势',                     chart_paths.get('mvrv')),
        ('😱', '恐惧贪婪指数趋势',                chart_paths.get('fg')),
        ('📈', 'ETF 资金流（近20个交易日）',       chart_paths.get('etf')),
        ('📊', 'BTC 永续合约 OI 趋势',            chart_paths.get('oi')),
        ('💹', '资金费率趋势',                     chart_paths.get('fr')),
    ]

    charts = []
    for icon, title, path in chart_defs:
        b64 = load_chart_b64(path)
        if b64:
            charts.append({'icon': icon, 'title': title, 'b64': b64})

    # ---- 近7天趋势 ----
    trend_rows = []
    for r in recent:
        price = f"${r['price_usd']:,.0f}" if r.get('price_usd') else '-'
        mvrv = f"{r['mvrv']:.4f}" if r.get('mvrv') else '-'
        fg = str(r['fear_greed_value']) if r.get('fear_greed_value') is not None else '-'
        etf = f"+${r['etf_net_flow_m']:.0f}M" if r.get('etf_net_flow_m') is not None else '-'
        fr = f"{r['funding_rate']:.6f}" if r.get('funding_rate') is not None else '-'
        flow_in = r.get('exchange_flow_in')
        flow_out = r.get('exchange_flow_out')
        net_flow = f"{flow_out - flow_in:,.0f} BTC" if flow_in and flow_out else '-'
        trend_rows.append({
            'date': r['date'], 'price': price, 'mvrv': mvrv,
            'fg': fg, 'etf': etf, 'fr': fr, 'net_flow': net_flow,
        })

    # ---- 筑底清单颜色 ----
    signals_met = checklist['signals_met']
    signal_color = '#27ae60' if signals_met >= 3 else '#e67e22' if signals_met >= 2 else '#e74c3c'
    signal_text = '可分批建仓' if signals_met >= 3 else '密切关注' if signals_met >= 2 else '观望'

    # ---- 见顶清单 v2（六维三态）----
    try:
        top = check_top_checklist()
        dim_icon = {'warn': '⚠️', 'turn': '🔴', 'green': '🟢', None: '⏳'}
        dim_color = {'turn': '#c0392b', 'warn': '#e67e22', 'green': '#27ae60', None: '#999'}
        top_dims = [{
            'icon': dim_icon.get(d['state'], '·'),
            'name': d['name'],
            'value': str(d.get('value')) if d.get('value') is not None else '-',
            'note': d['note'],
            'color': dim_color.get(d['state'], '#999'),
        } for d in top['dims']]
        top_level_text = {
            'confirm': f"顶部确认 · {top['action']}",
            'region': f"顶部区域 · {top['action']}",
            'normal': top['action'],
        }[top['level']]
        top_color = {'confirm': '#c0392b', 'region': '#e67e22', 'normal': '#27ae60'}[top['level']]
        top_cycle = top['cycle']
    except Exception as _e:
        top_dims, top_level_text, top_color, top_cycle = [], f'计算异常: {_e}', '#999', None

    # ---- 韩立提醒 ----
    hint = ''
    if len(recent) >= 2 and latest.get('price_usd') and recent[-1].get('price_usd'):
        pct = round((latest['price_usd'] / recent[-1]['price_usd'] - 1) * 100, 1)
        hint = f"7天涨{pct}%，"
    mvrv_str = f'{latest["mvrv"]:.2f}' if latest.get('mvrv') else '-'
    fg_str = latest.get('fear_greed_value', '-')
    hint += f"MVRV {mvrv_str} + 恐惧贪婪 {fg_str}，短期过热。不建议追高，回调至 $70K-72K 可考虑分批。"

    return {
        'target_date': target_date,
        'latest': latest,
        'signals': signals,
        'signals_met': signals_met,
        'signal_color': signal_color,
        'signal_text': signal_text,
        'top_dims': top_dims,
        'top_level_text': top_level_text,
        'top_color': top_color,
        'top_cycle': top_cycle,
        'charts': charts,
        'trend_rows': trend_rows,
        'hanli_hint': hint,
        'generated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def generate_html_report(target_date: str = None, chart_path: str = None,
                         mvrv_chart_path: str = None, fg_chart_path: str = None,
                         oi_chart_path: str = None, fr_chart_path: str = None) -> str:
    """生成完整 HTML 报告，返回文件路径"""
    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    chart_paths = {
        'etf': chart_path,
        'mvrv': mvrv_chart_path,
        'fg': fg_chart_path,
        'oi': oi_chart_path,
        'fr': fr_chart_path,
    }

    data = build_template_data(target_date, chart_paths)

    # Jinja2 渲染
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    env.filters['thousand_sep'] = lambda v: f'{v:,}'
    template = env.get_template('report_template.html')
    html = template.render(**data)

    # 输出
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = str(OUTPUT_DIR / f'btc_report_{target_date}.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    latest_path = str(OUTPUT_DIR / 'btc_report_latest.html')
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'✅ HTML 报告已保存: {output_path}')
    return output_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='BTC HTML 报告生成')
    parser.add_argument('--date', default=None)
    parser.add_argument('--chart', default=str(OUTPUT_DIR / 'etf_flows_latest.png'))
    parser.add_argument('--mvrv-chart', default=str(OUTPUT_DIR / 'mvrv_latest.png'))
    parser.add_argument('--fg-chart', default=str(OUTPUT_DIR / 'fear_greed_latest.png'))
    parser.add_argument('--oi-chart', default=str(OUTPUT_DIR / 'oi_trend_latest.png'))
    parser.add_argument('--fr-chart', default=str(OUTPUT_DIR / 'funding_rate_latest.png'))
    args = parser.parse_args()
    generate_html_report(args.date, args.chart, args.mvrv_chart, args.fg_chart, args.oi_chart, args.fr_chart)
