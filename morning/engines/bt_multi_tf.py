#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多周期超跌起爆回测：4h / 1h / 30m / 15m（日线对照已在 bt_oversold.py）。

与日线版唯一的差别：**时间参数按等价缩放，幅度参数原样保留**——
  60天窗口 / 6天RSI / 10天冷却 / f20天 / C2滚动30天 全部按周期换算根数；
  -30% 跌幅 / +5% 离底 / 75% 弹回 / -10% 硬止损 / 0.1%双边费 不变。
这保证四个周期是"同一策略的不同显微镜倍率"，而不是四个不同策略。

币池（用户 2026-09-20 拍板）：头部 7 币 BTC/ETH/BNB/SOL/XRP/DOGE/LINK + AR（用户指定保留）。
数据窗：1d/4h/1h 全历史；30m 近 4 年；15m 近 2 年（控制拉取量）。
"""
import json
import os
import sys
import urllib.request
import datetime as dt

import numpy as np
import pandas as pd

PROXY = 'http://127.0.0.1:7890'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': PROXY, 'http': PROXY}))
BASE = 'https://api.binance.com/api/v3/klines'
HERE = os.path.dirname(os.path.abspath(__file__))

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'ARUSDT']
TFS = {                      # 周期: (binance interval, 拉取起点)
    '4h':  ('4h',  None),
    '1h':  ('1h',  None),
    '30m': ('30m', '2022-09-01'),
    '15m': ('15m', '2024-09-01'),
    '1w':  ('1w',  None),
    '1M':  ('1M',  None),    # 大写 M = 月线（1m 是分钟）
}
TF_HOURS = {'1d': 24, '4h': 4, '1h': 1, '30m': 0.5, '15m': 0.25, '1w': 168, '1M': 720}

# 幅度参数（不随周期变）
DROP, OFFLOW, BOUNCE, HARD, FEE = -0.30, 0.05, 0.75, -0.10, 0.002


def scaled(tf, days):
    return max(2, round(days * 24 / TF_HOURS[tf]))


def fetch(sym, interval, start_ms=0):
    out = []
    while True:
        url = f'{BASE}?symbol={sym}&interval={interval}&limit=1000&startTime={start_ms}'
        with opener.open(url, timeout=30) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        out += batch
        start_ms = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    rows = []
    for k in out:
        day = dt.datetime.fromtimestamp(k[0] / 1000, dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
        rows.append((day, float(k[4])))
    seen, dedup = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            dedup.append(r)
    return dedup


def load_tf(tf):
    path = os.path.join(HERE, f'klines_{tf}.json')
    cache = json.load(open(path)) if os.path.exists(path) else {}
    interval, since = TFS[tf]
    start_ms = int(dt.datetime.strptime(since, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc).timestamp() * 1000) if since else 0
    for sym in SYMBOLS:
        if sym in cache:
            continue
        try:
            rows = fetch(sym, interval, start_ms)
            cache[sym] = rows
            json.dump(cache, open(path, 'w'))   # 逐币落盘，中断不丢进度
            print(f'  {tf} {sym}: {len(rows)} 根 ({rows[0][0]} ~ {rows[-1][0]})', flush=True)
        except Exception as e:
            print(f'  {tf} {sym}: 失败 {e}', flush=True)
    return cache


def backtest_one(closes_arr, tf):
    """返回 (事件列表, C2交易列表)。全部向量化；冷却逐候选处理。"""
    c = pd.Series(closes_arr, dtype=float)
    n60, n6 = scaled(tf, 60), scaled(tf, 6)
    cool, f20n, roll_n = scaled(tf, 10), scaled(tf, 20), scaled(tf, 30)

    ch = c.diff()
    rsi = (ch.clip(lower=0).ewm(alpha=1 / n6, adjust=False).mean()
           / ch.abs().ewm(alpha=1 / n6, adjust=False).mean() * 100)
    hi60 = c.rolling(n60).max().shift(1)   # 不含当根，与日线版口径一致
    lo60 = c.rolling(n60).min().shift(1)
    drop = c / hi60 - 1
    offlow = c / lo60 - 1
    bounce = (c - lo60) / (hi60 - lo60)
    cross = (rsi.shift(1) < 40) & (rsi >= 40)
    cand = cross & (drop <= DROP) & (offlow >= OFFLOW) & (bounce <= BOUNCE)

    idxs = np.flatnonzero(cand.to_numpy())
    events, last = [], -10**9
    for i in idxs:
        if i - last < cool:
            continue
        last = i
        events.append(int(i))

    # C2：止损线 = max(buy*0.9, 全局滚动 roll_n 根最低(不含当根))
    rollmin = c.rolling(roll_n).min().shift(1).to_numpy()
    cs = c.to_numpy()
    trades = []
    for i in events:
        sig_chg = cs[i] / cs[i - 1] - 1 if i > 0 else 0
        buy_i = i + 2 if sig_chg > 0.05 else i + 1   # T0 口径：当日买，>5% 顺延
        if buy_i >= len(cs):
            continue
        buy = cs[buy_i]
        stopline = np.maximum(buy * (1 + HARD), rollmin)          # 逐根
        seg = cs[buy_i + 1:] < stopline[buy_i + 1:]
        hit = np.flatnonzero(seg)
        if len(hit):
            j = buy_i + 1 + hit[0]
            exit_px, reason = cs[j], 'stop'
        else:
            j, exit_px, reason = len(cs) - 1, cs[-1], 'eod'
        pnl = (exit_px / buy - 1) * 100 - FEE * 100
        f20_i = min(buy_i + f20n, len(cs) - 1)
        f20 = (cs[f20_i] / buy - 1) * 100
        mae = (cs[buy_i:f20_i + 1].min() / buy - 1) * 100
        trades.append({'i': i, 'buy_i': buy_i, 'exit_i': j, 'pnl': pnl,
                       'hold': j - buy_i, 'reason': reason, 'f20': f20, 'mae': mae})
    return events, trades


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'n={len(v):4d} 均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}% 最差{v[0]:+7.1f}%'


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    data = {}
    if mode in ('fetch', 'all'):
        for tf in TFS:
            print(f'拉取 {tf} ...', flush=True)
            data[tf] = load_tf(tf)
    if mode in ('run', 'all'):
        for tf in TFS:
            path = os.path.join(HERE, f'klines_{tf}.json')
            data[tf] = json.load(open(path))

        # 日线对照（缓存复用）
        d1 = json.load(open(os.path.join(HERE, 'data', 'klines_cache.json')))
        data['1d'] = {s: [(r[0], r[4]) for r in d1[s]] for s in SYMBOLS if s in d1}

        all_ev, all_tr = {}, {}
        for tf in sorted(data, key=lambda x: -TF_HOURS[x]):
            for sym in SYMBOLS:
                if sym not in data[tf]:
                    continue
                arr = np.array([r[1] for r in data[tf][sym]])
                if len(arr) < scaled(tf, 70) + 10:
                    continue
                ev, tr = backtest_one(arr, tf)
                all_ev[(tf, sym)], all_tr[(tf, sym)] = ev, tr

        print('\n================ 分周期汇总（T0 建仓，C2 简化卖出） ================')
        print(f'{"周期":5s} {"事件研究 f20":52s}   {"MAE":40s}   {"C2 每笔":44s}')
        for tf in sorted(data, key=lambda x: -TF_HOURS[x]):
            f20s = [t['f20'] for (t2, _s), tr in all_tr.items() if t2 == tf for t in tr]
            maes = [t['mae'] for (t2, _s), tr in all_tr.items() if t2 == tf for t in tr]
            pnls = [t['pnl'] for (t2, _s), tr in all_tr.items() if t2 == tf for t in tr]
            holds = [t['hold'] for (t2, _s), tr in all_tr.items() if t2 == tf for t in tr]
            n_ev = sum(len(v) for (t2, _), v in all_ev.items() if t2 == tf)
            print(f'{tf:5s} {st(f20s):52s}   {st(maes):40s}   {st(pnls):44s} 持有{sum(holds)/max(len(holds),1):.0f}根 信号{n_ev}')

        print('\n================ 分周期 × 分币（f20 均值/中位/胜率 | C2 均值/净值） ================')
        print(f'{"币":9s} ' + ' '.join(f'{tf:^28s}' for tf in ['1d', '4h', '1h', '1w', '1M']))
        for sym in SYMBOLS:
            cells = []
            for tf in ['1d', '4h', '1h', '1w', '1M']:
                tr = all_tr.get((tf, sym), [])
                if not tr:
                    cells.append(f'{"—":^28s}')
                    continue
                f20s = [t['f20'] for t in tr]
                pnls = [t['pnl'] for t in tr]
                nav = 1.0
                for t in tr:
                    nav *= 1 + t['pnl'] / 100
                win = sum(1 for x in f20s if x > 0) / len(f20s) * 100
                nav_s = f'{nav:.1f}x' if nav >= 1 else f'{nav:.2f}x'
                cells.append(f'{sum(f20s)/len(f20s):+5.1f}%/{win:3.0f}%|{sum(pnls)/len(pnls):+6.1f}%/{nav_s:>7s} ^')
            print(f'{sym:9s} ' + ' '.join(f'{c[:-2]:28s}' for c in cells))

        # 分年（4h 头部合并，检查牛市依赖是否跨周期成立）
        print('\n================ 4h 全部 8 币合并分年（C2 每笔均值/胜率/串行净值） ================')
        import collections
        by_year = collections.defaultdict(list)
        for (tf, sym), tr in all_tr.items():
            if tf != '4h':
                continue
            days = [r[0] for r in data['4h'][sym]]
            for t in tr:
                by_year[days[t['buy_i']][:4]].append(t['pnl'])
        for y in sorted(by_year):
            v = by_year[y]
            nav = 1.0
            for x in v:
                nav *= 1 + x / 100
            win = sum(1 for x in v if x > 0) / len(v) * 100
            print(f'  {y}: n={len(v):4d} 均值{sum(v)/len(v):+7.2f}% 胜率{win:4.1f}% 净值{nav:.2f}x')


if __name__ == '__main__':
    main()
