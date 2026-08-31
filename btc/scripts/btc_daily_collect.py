#!/usr/bin/env python3
"""BTC 信号监控 - 每日自动采集脚本

功能：
1. 自动采集 CoinMetrics（MVRV、价格、交易所流）
2. 自动采集 Fear & Greed Index
3. 自动采集 Binance（资金费率、未平仓量）
4. ETF 数据通过 Tavily 搜索获取（输出待处理）
5. 更新筑底清单状态
6. 生成每日摘要

用法：
    python3 btc_daily_collect.py                    # 采集今天数据
    python3 btc_daily_collect.py --date 2026-08-20  # 采集指定日期
"""

import json
import sys
import os
from datetime import datetime, date
from pathlib import Path

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from btc_monitor_db import (
    init_db, upsert_daily_metrics, log_collect,
    check_checklist, update_checklist_status, get_recent_metrics,
    update_auto_support, check_top_checklist, update_top_checklist_status
)
from btc_collector import collect_fear_greed, collect_binance, collect_binance_spot_klines, collect_mvrv_bitcoindata, collect_all_top_indicators


def collect_etf_via_tavily(target_date: str) -> dict:
    """通过 Tavily 搜索获取 ETF 数据
    
    注意：Tavily API 需要通过 Agent 调用，这里生成搜索命令供 Agent 使用
    返回需要手动补充的提示信息
    """
    # 计算搜索日期格式（英文月 日, 年）
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    month_day = dt.strftime("%b %d")  # e.g., "Aug 20"
    year = dt.strftime("%Y")
    
    search_query = f"Bitcoin spot ETF net flow {month_day} {year}"
    
    return {
        "source": "etf",
        "status": "need_agent",
        "search_query": search_query,
        "message": f"需要通过 Tavily 搜索: {search_query}"
    }


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


def generate_daily_report(target_date: str, metrics: dict, checklist: dict) -> str:
    """生成每日报告 Markdown"""
    
    price_str = f"${metrics['price_usd']:,.2f}" if metrics.get('price_usd') is not None else '-'
    mvrv_str = f"{metrics['mvrv']:.4f}" if metrics.get('mvrv') is not None else '-'
    fg_str = f"{metrics.get('fear_greed_value', '-')} ({metrics.get('fear_greed_label', '-')})"
    fr_str = f"{metrics['funding_rate']:.6f}" if metrics.get('funding_rate') is not None else '-'
    oi_str = f"{metrics['open_interest']:,.2f} BTC" if metrics.get('open_interest') is not None else '-'
    flow_in_str = f"{metrics['exchange_flow_in']:,.0f} BTC" if metrics.get('exchange_flow_in') is not None else '-'
    flow_out_str = f"{metrics['exchange_flow_out']:,.0f} BTC" if metrics.get('exchange_flow_out') is not None else '-'
    
    report = f"""# BTC 每日监控报告

**日期**: {target_date}  
**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## 📊 核心指标

| 指标 | 数值 | 状态 |
|------|------|------|
| **BTC 价格** | {price_str} | - |
| **MVRV** | {mvrv_str} | {'✅ 1.0-1.2' if metrics.get('mvrv') and 1.0 <= metrics['mvrv'] <= 1.2 else '❌ 目标 1.0-1.2'} |
| **恐惧贪婪指数** | {fg_str} | {'✅ <15' if metrics.get('fear_greed_value') and metrics['fear_greed_value'] < 15 else '❌ 目标 <15'} |
| **ETF 单日净流入** | {'$' + str(round(metrics['etf_net_flow_m'], 1)) + 'M' if metrics.get('etf_net_flow_m') is not None else '待补充'} | - |
| **ETF 总 AUM** | {'$' + str(round(metrics['etf_total_aum_b'], 2)) + 'B' if metrics.get('etf_total_aum_b') is not None else '待补充'} | - |
| **资金费率** | {fr_str} | {'✅ >0' if metrics.get('funding_rate') and metrics['funding_rate'] > 0 else '❌ 目标 >0'} |
| **未平仓量** | {oi_str} | - |
| **交易所流入** | {flow_in_str} | - |
| **交易所流出** | {flow_out_str} | - |

---

## 🎯 筑底清单状态

**满足信号数: {checklist['signals_met']}/6**

| # | 信号 | 当前值 | 目标 | 状态 |
|---|------|--------|------|------|
"""
    
    details = checklist.get('details', {})
    signal_names = {
        '1_mvrv': 'MVRV 回落',
        '2_fear_greed': '恐惧贪婪极值',
        '3_etf_inflow': 'ETF 连续流入',
        '4_spot_perp': '现货+永续需求',
        '5_second_test': '二次探底不破低',
        '6_time_window': '时间窗口 Q4'
    }
    
    for key, name in signal_names.items():
        d = details.get(key, {})
        status = "✅" if d.get('status') else "❌"
        value = d.get('value', d.get('inflow_days', d.get('consecutive_days', d.get('period_low', d.get('current', '-')))))
        target = d.get('target', d.get('target_days', d.get('support', '-')))
        
        # 格式化值
        if key == '1_mvrv' and isinstance(value, float):
            value = f"{value:.4f}"
        elif key == '2_fear_greed' and isinstance(value, int):
            value = f"{value}"
        elif key == '3_etf_inflow':
            total = d.get('total_trading_days', 20)
            value = f"{value}/{total}天"
        elif key == '4_spot_perp':
            value = f"{value}天"
        elif key == '5_second_test' and isinstance(value, float):
            value = f"${value:,.0f}"
        
        report += f"| {key.split('_')[0]} | {name} | {value} | {target} | {status} |\n"
    
    # 操作建议
    action = "观望"
    if checklist['signals_met'] >= 3:
        action = "🟢 可开始分批建仓"
    elif checklist['signals_met'] >= 2:
        action = "🟡 密切关注"

    # ---- 见顶清单 v2 区块 ----
    top_section = ""
    try:
        top = check_top_checklist()
        state_icon = {"confirm": "🔴", "region": "⚠️", "normal": "🟢"}.get(top["level"], "·")
        dim_icon = {"warn": "⚠️ 过热", "turn": "🔴 转折", "green": "🟢 正常", None: "⏳ 无数据"}
        top_section += f"""
---

## 🔺 见顶清单 v2（六维三态）

**{state_icon} {top['action']}** · 过热 {top['overheat_cnt']} 维 / 转折 {top['turn_cnt']} 维（硬信号转折 {top['hard_turn']}）· 数据覆盖 {top['data_ok']}/6 维

| 维度 | 状态 | 当前值 | 判定说明 |
|------|------|--------|----------|
"""
        for d in top["dims"]:
            val = d.get("value")
            val_str = str(val) if val is not None else "-"
            top_section += f"| {d['name']} | {dim_icon.get(d['state'], '·')} | {val_str} | {d['note']} |\n"
        cyc = top["cycle"]
        top_section += f"""
**🕐 减半周期钟**: {cyc['phase']}
下轮减半 ≈ {cyc['next_halving']}（还剩 {cyc['days_to_next_halving']} 天），理论顶部高危窗 **{cyc['next_high_risk_window']}**

> 触发规则：⚠️≥2 维过热 → 停止加仓；🔴≥2 维转折且含资金流/杠杆硬信号 → 执行离场。
> 估值/情绪慢变量单独永不触发行动。参数：MVRV-Z 警戒线 {4.0}、SOPR 转折连续 {3} 天。
"""
    except Exception as e:
        top_section = f"\n---\n\n## 🔺 见顶清单 v2\n\n⚠️ 计算异常: {e}\n"

    report += f"""
**操作建议**: {action}
{top_section}
---

## 📈 近7天趋势

"""
    
    recent = get_recent_metrics(7)
    if recent:
        report += "| 日期 | 价格 | MVRV | 恐惧贪婪 | ETF净流入 | 资金费率 |\n"
        report += "|------|------|------|----------|-----------|----------|\n"
        for r in recent:
            price = f"${r['price_usd']:,.0f}" if r.get('price_usd') else '-'
            mvrv = f"{r['mvrv']:.4f}" if r.get('mvrv') else '-'
            fg = str(r['fear_greed_value']) if r.get('fear_greed_value') is not None else '-'
            etf = f"${r['etf_net_flow_m']:.1f}M" if r.get('etf_net_flow_m') is not None else '-'
            fr = f"{r['funding_rate']:.6f}" if r.get('funding_rate') is not None else '-'
            report += f"| {r['date']} | {price} | {mvrv} | {fg} | {etf} | {fr} |\n"
    
    report += f"""
---

## 📝 备注

- 数据源: Binance 现货日K (价格/现货量), bitcoin-data.com (MVRV, T+1), alternative.me (恐惧贪婪), Binance (资金费率/未平仓), CoinMarketCap (ETF)
- Binance 数据通过 Clash 代理获取
- ETF 数据需通过 Tavily 搜索补充
- 筑底清单满足 3/6 以上时触发建仓提醒

---

*报告由 BTC Monitor 自动生成*
"""
    
    return report


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="BTC 每日数据采集")
    parser.add_argument("--date", default=date.today().isoformat(), 
                        help="采集日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--etf-flow", type=float, help="ETF 单日净流入（百万美元）")
    parser.add_argument("--etf-aum", type=float, help="ETF 总资产净值（十亿美元）")
    parser.add_argument("--report", action="store_true", help="生成每日报告")
    parser.add_argument("--no-chart", action="store_true", help="不生成 ETF 图表")
    parser.add_argument("--no-feishu", action="store_true", help="不推送到飞书")
    parser.add_argument("--output-dir", default=str(Path(__file__).parent.parent / "reports"),
                        help="报告输出目录")
    
    args = parser.parse_args()
    
    target_date = args.date
    print(f"\n{'='*50}")
    print(f"🔄 BTC 每日采集 - {target_date}")
    print(f"{'='*50}\n")
    
    # 初始化数据库
    init_db()
    
    # 合并数据
    metrics = {"date": target_date}
    
    # 1. Binance 现货日K：价格 + 成交量（CoinMetrics 已退役；全量回填历史，幂等）
    print("📡 [1/4] Binance 现货K线（价格+成交量）...")
    try:
        r1 = collect_binance_spot_klines()
        log_collect("binance_spot_klines", r1["status"], r1.get("message", ""))
        if r1["status"] == "ok":
            for row in r1["data"]:
                # 历史行补写：COALESCE 保护 FG/funding 等字段不被抹除
                upsert_daily_metrics({"date": row["date"],
                                      "price_usd": row["price_usd"],
                                      "spot_volume": row["spot_volume"]})
            if r1.get("latest"):
                # 今天行写实时价（二次探底的"当前价"判定用它；次日自动修正为收盘价）
                metrics.update({"price_usd": r1["latest"]["price_usd"]})
            lv = r1["data"][-1]
            print(f"   ✅ 覆盖 {r1['count']} 天（{lv['date']} 收盘 ${lv['price_usd']:,.0f} / 量 {lv['spot_volume']/1e9:.2f}B）")
        else:
            print(f"   ⚠️ {r1.get('message')}")
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        log_collect("binance_spot_klines", "error", str(e))

    # 1.6 MVRV（bitcoin-data.com 替代 CoinMetrics；全量回填，幂等，T+1 出数）
    print("📡 [1.6/4] MVRV（bitcoin-data.com）...")
    try:
        r16 = collect_mvrv_bitcoindata()
        log_collect("bitcoindata_mvrv", r16["status"], r16.get("message", ""))
        if r16["status"] == "ok":
            for row in r16["data"]:
                upsert_daily_metrics({"date": row["date"], "mvrv": row["mvrv"]})
            mv = r16["data"][-1]
            print(f"   ✅ 覆盖 {r16['count']} 天（最新 {mv['date']}: MVRV {mv['mvrv']:.4f}）")
        else:
            print(f"   ⚠️ {r16.get('message')}")
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        log_collect("bitcoindata_mvrv", "error", str(e))

    # 1.7 见顶系统 v2 链上指标（bitcoin-data.com 六端点；限流友好：间隔8s、429熔断、增量续传）
    print("📡 [1.7/4] 见顶链上指标（mvrv-z/sopr/puell/nupl/reserve-risk/realized-profit）...")
    try:
        top_summary = collect_all_top_indicators(interval_sec=8.0)
        log_collect("bitcoindata_top6",
                    "ok" if not top_summary["failed"] else ("rate_limited" if top_summary["rate_limited"] else "partial"),
                    f"ok={len(top_summary['ok'])} failed={len(top_summary['failed'])}")
        if not top_summary["ok"] and not top_summary["failed"]:
            print("   ⚠️ 无端点成功")
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        log_collect("bitcoindata_top6", "error", str(e))

    # 2. Fear & Greed
    print("📡 [2/4] Fear & Greed Index...")
    r2 = collect_fear_greed(target_date)
    log_collect("fear_greed", r2["status"], r2.get("message", ""))
    if r2["status"] == "ok":
        metrics.update(r2["data"])
        print(f"   ✅ 恐惧贪婪={r2['data']['fear_greed_value']} ({r2['data']['fear_greed_label']})")
    else:
        print(f"   ⚠️ {r2['message']}")
    
    # 3. Binance
    print("📡 [3/4] Binance...")
    r3 = collect_binance()
    log_collect("binance", r3["status"], r3.get("message", ""))
    if r3["status"] == "ok":
        metrics.update(r3["data"])
        print(f"   ✅ 资金费率={r3['data']['funding_rate']:.6f}, 未平仓={r3['data']['open_interest']:,.2f} BTC")
    else:
        print(f"   ⚠️ {r3['message']}")
    
    # 3.5 同步 OI 历史数据到 oi_daily 表
    try:
        import requests as _req
        _url = "https://fapi.binance.com/futures/data/openInterestHist"
        _params = {"symbol": "BTCUSDT", "period": "1d", "limit": 31}
        _r = _req.get(_url, params=_params, proxies={"https": "http://127.0.0.1:7890"}, timeout=15)
        if _r.status_code == 200:
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(str(Path(__file__).parent.parent / "data" / "btc_monitor.db"))
            _count = 0
            for _d in _r.json():
                from datetime import datetime as _dt
                _date = _dt.fromtimestamp(_d["timestamp"] / 1000).strftime("%Y-%m-%d")
                _conn.execute(
                    "INSERT OR REPLACE INTO oi_daily (date, oi_btc, oi_usd) VALUES (?, ?, ?)",
                    (_date, float(_d["sumOpenInterest"]), float(_d["sumOpenInterestValue"])),
                )
                _count += 1
            _conn.commit()
            _conn.close()
            print(f"   ✅ OI 历史同步: {_count} 条")
    except Exception as e:
        print(f"   ⚠️ OI 历史同步失败: {e}")
    
    # 3.6 同步资金费率历史数据
    try:
        import requests as _req
        _url = "https://fapi.binance.com/fapi/v1/fundingRate"
        _params = {"symbol": "BTCUSDT", "limit": 1000}
        _r = _req.get(_url, params=_params, proxies={"https": "http://127.0.0.1:7890"}, timeout=15)
        if _r.status_code == 200:
            import sqlite3 as _sqlite3
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            _conn = _sqlite3.connect(str(Path(__file__).parent.parent / "data" / "btc_monitor.db"))
            _daily = {}
            for _d in _r.json():
                _dt8 = _dt.fromtimestamp(_d["fundingTime"] / 1000, tz=_tz(_td(hours=8)))
                _date = _dt8.strftime("%Y-%m-%d")
                _rate = float(_d["fundingRate"])
                if _date not in _daily:
                    _daily[_date] = []
                _daily[_date].append(_rate)
            _count = 0
            for _date, _rates in _daily.items():
                _avg = sum(_rates) / len(_rates)
                _conn.execute(
                    """INSERT INTO daily_metrics (date, funding_rate) VALUES (?, ?)
                       ON CONFLICT(date) DO UPDATE SET funding_rate = excluded.funding_rate""",
                    (_date, _avg),
                )
                _count += 1
            _conn.commit()
            _conn.close()
            print(f"   ✅ 资金费率历史同步: {_count} 天")
    except Exception as e:
        print(f"   ⚠️ 资金费率历史同步失败: {e}")
    
    # 4. ETF（外部传入或提示需要 Agent）
    print("📡 [4/4] ETF 数据...")
    if args.etf_flow is not None or args.etf_aum is not None:
        metrics["etf_net_flow_m"] = args.etf_flow
        metrics["etf_total_aum_b"] = args.etf_aum
        print(f"   ✅ 净流入=${args.etf_flow}M, AUM=${args.etf_aum}B")
        log_collect("etf", "ok", f"flow={args.etf_flow}, aum={args.etf_aum}")
    else:
        etf_hint = collect_etf_via_tavily(target_date)
        metrics["etf_net_flow_m"] = None
        metrics["etf_total_aum_b"] = None
        print(f"   ⏳ {etf_hint['message']}")
        log_collect("etf", "pending", etf_hint["search_query"])
    
    # 写入数据库
    print("\n💾 写入数据库...")
    upsert_daily_metrics(metrics)
    print("   ✅ daily_metrics 已更新")
    
    # 重算自动前低（二次探底信号用，manual 设置优先不受影响）
    print("\n📍 更新关键位（swing-low 前低）...")
    sup = update_auto_support()
    if sup.get("level"):
        print(f"   ✅ 前低 ${sup['level']:,.0f}（锚定 {sup['anchor_date']}，此后反弹 {sup['rebound_pct']*100:.1f}%，窗口 {sup['window_days']} 天）")
    else:
        print(f"   ⚠️ 未识别前低: {sup.get('reason')}")

    # 更新筑底清单
    print("\n🎯 检查筑底清单...")
    checklist = update_checklist_status()
    print(f"   满足信号数: {checklist['signals_met']}/6")

    if checklist['signals_met'] >= 3:
        print("   🟢 ⚠️ 信号触发：可开始分批建仓！")

    # 见顶清单 v2（六维三态；当前周期已过顶，系统静默备勤但每日照常体检）
    print("\n🔺 检查见顶清单 v2...")
    try:
        top = update_top_checklist_status()
        state_icon = {"confirm": "🔴", "region": "⚠️ ", "normal": "🟢"}.get(top["level"], "·")
        print(f"   {state_icon} 过热{top['overheat_cnt']}维 / 转折{top['turn_cnt']}维"
              f"(硬信号{top['hard_turn']}) · 数据{top['data_ok']}/6维 → {top['action']}")
        for d in top["dims"]:
            icon = {"warn": "⚠️", "turn": "🔴", "green": "🟢", None: "⏳"}.get(d["state"], "·")
            val = d.get("value")
            print(f"      {icon} {d['name']:<18} {val if val is not None else '-'} | {d['note'][:60]}")
        print(f"   🕐 周期钟: {top['cycle']['phase']}")
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        top = None
    
    # 生成 ETF 图表
    chart_path = None
    if not args.no_chart:
        try:
            from btc_etf_chart import render_chart
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            chart_path = str(output_dir / "etf_flows_latest.png")
            print(f"\n📊 生成 ETF 图表...")
            render_chart(output_path=chart_path)
        except Exception as e:
            print(f"   ⚠️ 图表生成失败: {e}")
    
    # 生成 MVRV 趋势图
    mvrv_chart_path = None
    if not args.no_chart:
        try:
            from btc_mvrv_chart import fetch_mvrv_data, render_chart as render_mvrv_chart
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            mvrv_chart_path = str(output_dir / "mvrv_latest.png")
            print(f"\n📊 生成 MVRV 趋势图...")
            mvrv_data = fetch_mvrv_data(90)
            if mvrv_data:
                render_mvrv_chart(mvrv_data, output_path=mvrv_chart_path)
        except Exception as e:
            print(f"   ⚠️ MVRV 图表生成失败: {e}")
    
    # 生成恐惧贪婪趋势图
    fg_chart_path = None
    if not args.no_chart:
        try:
            from btc_fear_greed_chart import fetch_fg_data, render_chart as render_fg_chart
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            fg_chart_path = str(output_dir / "fear_greed_latest.png")
            print(f"\n📊 生成恐惧贪婪趋势图...")
            fg_data = fetch_fg_data(90)
            if fg_data:
                render_fg_chart(fg_data, output_path=fg_chart_path)
        except Exception as e:
            print(f"   ⚠️ 恐惧贪婪图表生成失败: {e}")
    
    # 生成 OI 趋势图
    oi_chart_path = None
    if not args.no_chart:
        try:
            from btc_oi_chart import fetch_oi_data, render_chart as render_oi_chart
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            oi_chart_path = str(output_dir / "oi_trend_latest.png")
            print(f"\n📊 生成 OI 趋势图...")
            oi_data = fetch_oi_data(180)
            render_oi_chart(oi_data, output_path=oi_chart_path)
        except Exception as e:
            print(f"   ⚠️ OI 图表生成失败: {e}")
    
    # 生成资金费率趋势图
    fr_chart_path = None
    if not args.no_chart:
        try:
            from btc_funding_chart import fetch_funding_data, render_chart as render_fr_chart
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            fr_chart_path = str(output_dir / "funding_rate_latest.png")
            print(f"\n📊 生成资金费率趋势图...")
            fr_data = fetch_funding_data(60)
            if fr_data:
                render_fr_chart(fr_data, output_path=fr_chart_path)
        except Exception as e:
            print(f"   ⚠️ 资金费率图表生成失败: {e}")
    
    # 生成 HTML 报告（含图表）
    html_path = None
    try:
        from btc_report_html import generate_html_report
        print(f"\n📄 生成 HTML 报告...")
        html_path = generate_html_report(target_date, chart_path, mvrv_chart_path, fg_chart_path, oi_chart_path, fr_chart_path)
    except Exception as e:
        print(f"   ⚠️ HTML 报告生成失败: {e}")
    
    # 生成报告
    if args.report:
        print("\n📄 生成报告...")
        report = generate_daily_report(target_date, metrics, checklist)
        
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{target_date}.md"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        
        print(f"   ✅ 报告已保存: {report_path}")
    
    # 飞书推送 HTML 报告
    if html_path and not args.no_feishu:
        try:
            from feishu_send_file import get_feishu_config, get_tenant_token, upload_file, send_file_message
            print(f"\n📱 推送到飞书...")
            app_id, app_secret, open_id = get_feishu_config()
            if not open_id:
                raise Exception("未指定接收人: 请设置环境变量 FEISHU_OPEN_ID 或在 config 中配置 open_id")
            token = get_tenant_token(app_id, app_secret)
            file_key, file_type = upload_file(token, html_path)
            send_file_message(token, open_id, file_key, file_type)
            print(f"   ✅ 飞书推送完成")
            
            # 推送文本摘要
            from feishu_send_text import send_text_message
            summary = generate_daily_report(target_date, metrics, checklist)
            send_text_message(token, open_id, summary)
            print(f"   ✅ 文本摘要已推送")
        except Exception as e:
            print(f"   ⚠️ 飞书推送失败: {e}")
    
    print(f"\n{'='*50}")
    print(f"✅ 采集完成: {target_date}")
    print(f"{'='*50}\n")
    
    return metrics, checklist, chart_path, html_path


if __name__ == "__main__":
    main()
