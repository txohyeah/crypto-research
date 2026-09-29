#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 5 币超跌起爆信号交互图（单文件 HTML，内嵌 echarts）。

口径与 bt_multi_tf.py 1d 完全一致：
  超跌: 距60日最高收盘 <= -30% | 离底: 距60日最低收盘 >= +5%
  弹回: (c-lo60)/(hi60-lo60) <= 75% | 起爆: RSI6 上穿 40 | 冷却 10 天
标记：红色朝上箭头 + 「超跌」二字。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt_goldencross_v2 import signals as gc_signals_v2  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ECHARTS = '/root/.qwenpaw/workspaces/default/temp_script/pullback_charts/echarts.min.js'
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT']
CN = {'BTCUSDT': 'BTC 比特币', 'ETHUSDT': 'ETH 以太坊', 'SOLUSDT': 'SOL Solana',
      'BNBUSDT': 'BNB 币安币', 'ARUSDT': 'AR Arweave'}


def signals_for(rows):
    c = pd.Series([r[4] for r in rows], dtype=float)
    ch = c.diff()
    rsi = (ch.clip(lower=0).ewm(alpha=1 / 6, adjust=False).mean()
           / ch.abs().ewm(alpha=1 / 6, adjust=False).mean() * 100)
    hi60 = c.rolling(60).max().shift(1)
    lo60 = c.rolling(60).min().shift(1)
    drop = c / hi60 - 1
    offlow = c / lo60 - 1
    bounce = (c - lo60) / (hi60 - lo60)
    cross = (rsi.shift(1) < 40) & (rsi >= 40)
    cand = cross & (drop <= -0.30) & (offlow >= 0.05) & (bounce <= 0.75)
    idxs = np.flatnonzero(cand.to_numpy())
    events, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        events.append(int(i))
    return events


def pack(rows, events, vols):
    dates = [r[0] for r in rows]
    k = [[round(r[1], 4), round(r[2], 4), round(r[3], 4), round(r[4], 4)] for r in rows]
    c = pd.Series([r[4] for r in rows], dtype=float)
    ma20 = [None if not np.isfinite(v) else round(v, 4) for v in c.rolling(20).mean()]
    ma60 = [None if not np.isfinite(v) else round(v, 4) for v in c.rolling(60).mean()]
    vmap = dict(vols)
    vol = [round(vmap.get(d, 0.0), 2) for d in dates]
    sig = [{'i': int(i), 'd': dates[i]} for i in events]
    return {'dates': dates, 'k': k, 'ma20': ma20, 'ma60': ma60, 'vol': vol, 'sig': sig}


def main():
    data = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_ohlc_1d.json')))
    voldata = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'klines_vol_1d.json')))
    gcv2 = json.load(open(os.path.join(os.path.dirname(HERE), 'data', 'goldencross_v2_signals.json')))
    packs, stats = {}, []
    for s in SYMS:
        ev = signals_for(data[s])
        vols = [(r[0], r[2]) for r in voldata[s]]
        # 金叉 v2 定稿口径信号（默认含 MA60 走平带 -0.10，2026-09-21 拍板）
        rows = data[s]
        c = pd.Series([r[4] for r in rows], dtype=float)
        dates_all = [r[0] for r in rows]
        gc_ev, _m20, _m60 = gc_signals_v2(c, dates_all)
        # v3 粘合金叉（即死未死，2026-09-21 并入金叉体系）：多头排列中 MA20 回粘 MA60 未死叉后重新拉开
        gap = (c.rolling(20).mean() - c.rolling(60).mean()) / c.rolling(60).mean() * 100
        s20v = c.rolling(20).mean().diff() / c.rolling(20).mean().shift(1) * 100
        no_die = ((c.rolling(20).mean() - c.rolling(60).mean()).rolling(60, min_periods=60).min() >= 0)
        pin = gap.rolling(15, min_periods=1).min() <= 2.0
        revive = (gap > gap.shift(1)) & (gap.shift(1) > gap.shift(2)) & (s20v > 0) & (s20v <= 0.5) & (gap <= 2.0)
        v3_m = no_die & pin & revive
        v3_idxs = np.flatnonzero(v3_m.to_numpy())
        v3_ev, v3_last = [], -10**9
        for i in v3_idxs:
            if i - v3_last < 10: continue
            v3_last = i; v3_ev.append(int(i))
        # 陡上穿观察标记（2026-09-21 S20 上限放宽扫描产物）：MA20 上穿 MA60 当日 s20>0.5%，
        # 即金叉v2 原 MA20 上限杀掉的"陡金叉"。观察性质非策略信号：标注上穿分档 + MA60 双杀标志。
        ma20v = c.rolling(20).mean()
        ma60v = c.rolling(60).mean()
        s60v = ma60v.diff() / ma60v.shift(1) * 100
        cross_m = (ma20v.shift(1) <= ma60v.shift(1)) & (ma20v > ma60v)
        st_idxs = np.flatnonzero((cross_m & (s20v > 0.5)).to_numpy())
        def _grade(a):
            return '温陡' if a <= 0.75 else ('点火' if a <= 1.0 else ('暴拉' if a <= 1.5 else '极端'))
        steep = []
        for i in st_idxs:
            a, b = float(s20v.iloc[i]), float(s60v.iloc[i])
            steep.append({'i': int(i), 'd': dates_all[i],
                          't': _grade(a) + ('·60超' if b > 0.25 else ''),
                          's20': round(a, 2), 's60': round(b, 2)})
        packs[s] = pack(data[s], ev, vols)
        packs[s]['gc'] = [{'i': int(i), 'd': dates_all[i]} for i in gc_ev]
        packs[s]['v3'] = [{'i': int(i), 'd': dates_all[i]} for i in v3_ev]
        packs[s]['steep'] = steep
        # 卖出事件（v2 状态机：类型+日期，index 在此定位）
        idx_of = {d: i for i, d in enumerate(dates_all)}
        packs[s]['sell'] = [{'i': idx_of[x['d']], 'd': x['d'], 'tp': x['type']}
                            for x in gcv2[s]['sells']]
        sigs = packs[s]['sig']
        gcs = packs[s]['gc']
        v3s = packs[s]['v3']
        st = packs[s]['steep']
        last5 = ', '.join(x['d'] for x in sigs[-5:])
        gc_last5 = ', '.join(x['d'] for x in gcs[-5:])
        st_last5 = ', '.join(x['d'][2:] for x in st[-5:])
        stats.append((CN[s], len(sigs), sigs[-1]['d'] if sigs else '—', last5,
                      len(gcs), gc_last5, len(st), st_last5))
        print(f'{s}: 超跌 {len(sigs)} 个, 金叉v2定稿 {len(gcs)} 个, 粘合金叉 {len(v3s)} 个, '
              f'陡上穿观察 {len(st)} 个, 卖出 {len(packs[s]["sell"])} 个')

    json.dump({s: packs[s]['steep'] for s in SYMS},
              open(os.path.join(os.path.dirname(HERE), 'data', 'goldencross_steep_signals.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    js_data = json.dumps(packs, separators=(',', ':'), ensure_ascii=False)
    stat_rows = '\n'.join(
        f'<tr><td>{n}</td><td>{c}</td><td class="mono small">{h}</td>'
        f'<td>{gc}</td><td class="mono small">{gh}</td>'
        f'<td>{sn}</td><td class="mono small">{sh}</td></tr>'
        for n, c, l, h, gc, gh, sn, sh in stats)

    tpl = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>超跌起爆 + 平缓金叉 信号图 · BTC/ETH/SOL/BNB/AR（日线）</title>
<style>
  body { margin:0; background:#111418; color:#d8dce2; font-family:"PingFang SC","Microsoft YaHei",sans-serif; }
  header { padding:14px 20px 6px; }
  h1 { font-size:17px; margin:0 0 4px; color:#fff; }
  .meta { font-size:12px; color:#8b93a1; line-height:1.7; }
  .meta b { color:#e05252; }
  .tabs { padding:10px 20px 0; }
  .tabs button { background:#1d222b; color:#aab2bf; border:1px solid #2a313d; border-radius:6px 6px 0 0;
    padding:8px 22px; font-size:14px; cursor:pointer; margin-right:4px; }
  .tabs button.on { background:#2a313d; color:#fff; font-weight:600; }
  #chart { width:100%; height:78vh; min-height:520px; }
  table.stats { margin:6px 20px 18px; border-collapse:collapse; font-size:12.5px; }
  table.stats td, table.stats th { border:1px solid #2a313d; padding:5px 12px; text-align:left; }
  table.stats th { background:#1d222b; color:#aab2bf; font-weight:500; }
  .mono { font-family:Menlo,Consolas,monospace; }
  .small { font-size:11px; color:#8b93a1; }
</style>
</head>
<body>
<header>
  <h1>超跌起爆 + 平缓金叉 信号图 · 日线（币安 USDT，全历史）</h1>
  <div class="meta">
    <span style="color:#ff6b6b;font-weight:600">▲ 红·超跌</span>：距60日最高收盘 ≤ -30% ｜ 离底 ≥ +5% ｜ 弹回 ≤ 75% ｜ RSI6 上穿 40 ｜ 冷却 10 天<br>
    <span style="color:#f0b429;font-weight:600">▲ 金·金叉v2（定稿 2026-09-21）</span>：MA20 上穿 MA60 ｜ MA20 斜率 0~0.5%/根、MA60 斜率 (-0.10, 0.25]%（对称走平带：MA60 微跌 ≤0.10%/根 视为走平=0°——拐头初期单日符号是噪音；斜率=当日MA相对前一日的变化率）｜ 冷却 10 天<br>
    <span style="color:#26c6da;font-weight:600">▲ 青·粘合金叉（v3 即死未死，并入金叉体系 2026-09-21）</span>：无穿越事件的金叉变体——gap=(MA20-MA60)/MA60 ｜ 60 天内未死叉 ｜ 近 15 天 gap 曾 ≤2%（回粘；θ=2.0 实测定稿：1.0/1.5 漏掉 ETH 2020-07 粘合极值 1.98% 的大行情，2.0~2.5 参数平原）｜ gap 连扩 2 天 + MA20 斜率 0~0.5% + 触发时 gap ≤2%（刚抬头就买）｜ 全历史 4 例：ETH 2020-07-19 完整+19.2%、BTC 2025-07-09 +3.6%、BNB 2024-05-08 -2.3%、BNB 2024-05-22 -1.5%（合并体系净值 21.65→25.8x，低频观察层与金叉同框统计）<br>
    <span style="color:#ff922b;font-weight:600">▲ 橙·陡上穿（观察标记 2026-09-21，空心箭头=非策略信号、不入交易统计）</span>：MA20 上穿 MA60 当日 s20&gt;0.5%/根——金叉v2 原 MA20 上限（0~0.5）杀掉的"陡金叉"（S20 上限放宽扫描产物，参数未拍板，仅记录观察）｜ 按 s20 分档标注上穿性质：<b>温陡</b> 0.5~0.75（实测最差层：f20 均值 -1.2%，上不上下不下追进易回调）/ <b>点火</b> 0.75~1.0（精华层：ETH 2020-10-23 完整+118%、BTC 2021-08 主升浪起点，肉在长尾 f20 小完整大）/ <b>暴拉</b> 1.0~1.5（胜率 36% 赌徒层：SOL 2023-10-07 完整+126% 一笔撑全场）/ <b>极端</b> &gt;1.5（样本仅 5 笔不足为凭）｜ 标"·60超"=当日 MA60 斜率同超走平带上限 0.25（双杀案：BTC 2020-07-27 s20=0.95+s60=0.26，救它须动 MA60 上限而非 MA20）<br>
    卖出状态机：硬止损 -10% ｜ 浮盈 &lt;100%：跌破 MA20 卖一半、跌破 MA60 清仓 ｜ 浮盈曾 ≥100%：切移动止盈（回吐最高利润的 1/3 清仓）<br>
    <span style="color:#dfe3ea;font-weight:600">▼ 白·卖出</span>（金叉v2 持仓的离场点，朝下箭头+来源标注：卖半·破MA20 / 清仓·破MA60 / 止损-10% / 移动止盈 / 持仓中）<br>
    两者均为<b>买入信号日</b>标记（信号次日买入，当日涨幅&gt;5% 则顺延一日）。滚轮缩放，底部滑块平移，默认显示最近 600 根。
  </div>
</header>
<div class="tabs" id="tabs"></div>
<div id="chart"></div>
<table class="stats">
  <tr><th rowspan="2">币</th><th colspan="3">红 · 超跌起爆</th><th colspan="2">金 · 金叉v2（定稿·含走平带）</th><th colspan="2">橙 · 陡上穿（观察·未拍板）</th></tr>
  <tr><th>信号数</th><th>最近 5 次信号日期</th><th>信号数</th><th>最近 5 次信号日期</th><th>次数</th><th>最近 5 次日期</th></tr>
  __STATS__
</table>
<script>__ECHARTS__</script>
<script>
const DATA = __DATA__;
const SYMS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','ARUSDT'];
const CN = {BTCUSDT:'BTC 比特币',ETHUSDT:'ETH 以太坊',SOLUSDT:'SOL Solana',BNBUSDT:'BNB 币安币',ARUSDT:'AR Arweave'};
const UP = '#26a69a', DN = '#ef5350';
let chart = null, cur = null;

const ARROW = 'path://M5,0 L10,9.5 L6.4,9.5 L6.4,15 L3.6,15 L3.6,9.5 L0,9.5 Z';

function option(sym) {
  const d = DATA[sym];
  const marks = d.sig.map(s => ({
    coord: [s.d, d.k[s.i][2]],            // 信号日最低价
    symbol: ARROW, symbolSize: [13, 16], symbolRotate: 0, symbolOffset: [0, 20],
    itemStyle: { color: '#e03131' },
    label: { show: true, position: 'bottom', distance: 2, formatter: '超跌',
             color: '#ff6b6b', fontSize: 11, fontWeight: 600 }
  }));
  const gcMarks = (d.gc || []).map(s => ({
    coord: [s.d, d.k[s.i][3]],            // 金叉信号日收盘价
    symbol: ARROW, symbolSize: [13, 16], symbolRotate: 0, symbolOffset: [0, -22],
    itemStyle: { color: '#f0b429' },
    label: { show: true, position: 'top', distance: 2, formatter: '金叉',
             color: '#f0b429', fontSize: 11, fontWeight: 600 }
  }));
  const SELL_TXT = {half_ma20: '卖半·破MA20', ma60: '清仓·破MA60', stop: '止损-10%',
                    trail: '移动止盈', eod: '持仓中'};
  const v3Marks = (d.v3 || []).map(s => ({
    coord: [s.d, d.k[s.i][3]],            // 粘合金叉信号日收盘价
    symbol: ARROW, symbolSize: [13, 16], symbolRotate: 0, symbolOffset: [0, -22],
    itemStyle: { color: '#26c6da' },
    label: { show: true, position: 'top', distance: 2, formatter: '粘合金叉',
             color: '#26c6da', fontSize: 11, fontWeight: 600 }
  }));
  const steepMarks = (d.steep || []).map(s => ({
    coord: [s.d, d.k[s.i][3]],            // 陡上穿日收盘价
    symbol: ARROW, symbolSize: [10, 13], symbolRotate: 0, symbolOffset: [0, -40],
    itemStyle: { color: 'rgba(0,0,0,0)', borderColor: '#ff922b', borderWidth: 1.4 },
    label: { show: true, position: 'top', distance: 2, formatter: s.t,   // 分档：温陡/点火/暴拉/极端（·60超=MA60 双杀）
             color: '#ff922b', fontSize: 9.5, fontWeight: 600 }
  }));
  const sellMarks = (d.sell || []).map(s => {
    const full = s.tp !== 'half_ma20';    // 清仓类放最高一层，避免与"卖半"、陡上穿文字重叠
    return {
      coord: [s.d, d.k[s.i][1]],          // 出场日最高价
      symbol: ARROW, symbolSize: [11, 14], symbolRotate: 180,
      symbolOffset: [0, full ? -56 : -26],
      itemStyle: { color: '#dfe3ea' },
      label: { show: true, position: 'top', distance: 2, formatter: SELL_TXT[s.tp] || s.tp,
               color: '#dfe3ea', fontSize: 10, fontWeight: 600 }
    };
  });
  return {
    animation: false, backgroundColor: '#111418',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#2a313d' } },
      backgroundColor: 'rgba(20,24,30,.95)', borderColor: '#2a313d', textStyle: { color: '#d8dce2', fontSize: 12 } },
    legend: { data: ['MA20','MA60'], top: 4, textStyle: { color: '#8b93a1' }, icon: 'line' },
    grid: [ { left: 64, right: 20, top: 28, height: '62%' },
            { left: 64, right: 20, top: '76%', height: '14%' } ],
    xAxis: [
      { type: 'category', data: d.dates, gridIndex: 0, boundaryGap: true,
        axisLine: { lineStyle: { color: '#2a313d' } }, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: d.dates, gridIndex: 1, boundaryGap: true,
        axisLine: { lineStyle: { color: '#2a313d' } }, axisLabel: { color: '#8b93a1' }, axisTick: { show: false } } ],
    yAxis: [
      { scale: true, gridIndex: 0, axisLabel: { color: '#8b93a1' }, splitLine: { lineStyle: { color: '#1d222b' } } },
      { scale: true, gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } } ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: Math.max(0, 100 - 600 / d.dates.length * 100), end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], top: '93%', height: 18, start: Math.max(0, 100 - 600 / d.dates.length * 100), end: 100,
        borderColor: '#2a313d', fillerColor: 'rgba(224,49,49,.12)', handleStyle: { color: '#2a313d' },
        dataBackground: { lineStyle: { color: '#3a4250' }, areaStyle: { color: '#1d222b' } }, textStyle: { color: '#8b93a1' } } ],
    series: [
      { name: CN[sym], type: 'candlestick', data: d.k, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: UP, color0: DN, borderColor: UP, borderColor0: DN },
        markPoint: { data: marks.concat(gcMarks, v3Marks, steepMarks, sellMarks), animation: false },
        labelLayout: { hideOverlap: true }, z: 5 },
      { name: 'MA20', type: 'line', data: d.ma20, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { width: 1, color: '#e8b339' }, z: 2 },
      { name: 'MA60', type: 'line', data: d.ma60, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { width: 1, color: '#5b8def' }, z: 2 },
      { name: '成交量', type: 'bar', data: d.vol, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: '#3d4654' }, large: true, z: 1 }
    ]
  };
}

function show(sym) {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.sym === sym));
  if (!chart) chart = echarts.init(document.getElementById('chart'));
  if (cur) chart.clear();
  chart.setOption(option(sym));
  cur = sym;
}
window.addEventListener('resize', () => chart && chart.resize());
const tabsEl = document.getElementById('tabs');
SYMS.forEach(s => {
  const b = document.createElement('button');
  b.textContent = CN[s]; b.dataset.sym = s;
  b.onclick = () => show(s);
  tabsEl.appendChild(b);
});
show('BTCUSDT');
</script>
</body>
</html>"""

    ech = open(ECHARTS, encoding='utf-8').read()
    html = (tpl.replace('__ECHARTS__', ech)
               .replace('__DATA__', js_data)
               .replace('__STATS__', stat_rows))
    out = os.path.join(HERE, 'oversold_ignition_5coins.html')
    open(out, 'w', encoding='utf-8').write(html)
    print(f'written {out} ({os.path.getsize(out)/1e6:.1f} MB)')


if __name__ == '__main__':
    main()
