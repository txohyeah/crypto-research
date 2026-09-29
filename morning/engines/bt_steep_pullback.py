#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陡上穿（大斜率 MA20 金叉）买点改造回测：直接追买 vs 等回踩 MA20 再买。

用户提议（2026-09-21）：陡上穿不追，等回踩 MA20 后的起爆点买入，跌破 MA20 止损。
与已证伪的 bt_pullback（60 日趋势内回撤+RSI 上穿 50，无金叉前提）不同源——
这是对金叉体系的买点改造，触发前提=陡金叉。

口径：
  触发：金叉穿越日 s20 > 0.5（四档：温陡 0.5-0.75 / 点火 0.75-1.0 / 暴拉 1.0-1.5 / 极端 >1.5）
  等待：信号日次日起 30 根内首次 low <= MA20（实际触及）
  确认：触及日起 3 根内首次收盘 > MA20 → 次日收盘买入（对齐体系 T1 风格）
  卖出：收盘 < MA20 全清（用户口径，A/B 同尺公平对照）；双边 0.1%
  冷却：持仓期间不接受新触发；每信号只做一次
  对照：A=信号次日收盘直接追买（同尺卖出）→ 增量 = B - A
  错失：窗口内未回踩直接走掉的信号（A 能吃到、B 吃不到）
数据：现拉 6 币 1d（data-api.binance.vision）
"""
import json
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bt_goldencross_v2 import run_trade

POOL = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT', 'DOGEUSDT']
FEE = 0.002
WAIT_N, CONFIRM_N = 30, 3
TIERS = [(0.5, 0.75, '温陡'), (0.75, 1.0, '点火'), (1.0, 1.5, '暴拉'), (1.5, 99, '极端')]


def tier_of(s20):
    for lo, hi, name in TIERS:
        if lo < s20 <= hi:
            return name
    return None


def steep_crosses(c, dates):
    """全部穿越日 + s20 分档（不含冷却，全量枚举）。"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    cross = (ma20.shift(1) <= ma60.shift(1)) & (ma20 > ma60)
    m20a, m60a = ma20.to_numpy(), ma60.to_numpy()
    out = []
    for i in np.flatnonzero(cross.to_numpy()):
        sv = float(s20.iloc[i])
        if sv > 0.5:
            out.append((int(i), sv, tier_of(sv), m20a, m60a))
    return out


def trade_pullback(cs, lows, ma20, i):
    """等回踩买：返回 dict(sell 收益) 或状态 miss/failed。"""
    n = len(cs)
    touch = conf = buy_i = None
    for j in range(i + 1, min(i + 1 + WAIT_N, n)):
        if touch is None and lows[j] <= ma20[j]:
            touch = j
        if touch is not None and j >= touch and cs[j] > ma20[j]:
            conf = j
            buy_i = j + 1
            break
    if buy_i is None:
        if touch is None:
            return {'status': 'miss'}
        return {'status': 'failed'}
    if buy_i >= n:
        return {'status': 'eod_open', 'buy_i': buy_i}
    buy = cs[buy_i]
    exit_i = None
    for j in range(buy_i + 1, n):
        if cs[j] < ma20[j]:
            exit_i = j
            break
    if exit_i is None:
        exit_i = n - 1
    pnl = (cs[exit_i] / buy - 1) * 100 - FEE * 100
    return {'status': 'done', 'touch': touch, 'conf': conf, 'buy_i': buy_i,
            'hold': exit_i - buy_i, 'pnl': pnl, 'exit': exit_i}


def trade_direct(cs, ma20, i):
    """对照 A：次日收盘直接追买，同尺（收盘<MA20 清）。"""
    n = len(cs)
    buy_i = i + 1
    if buy_i >= n:
        return {'status': 'eod_open', 'buy_i': buy_i}
    buy = cs[buy_i]
    exit_i = None
    for j in range(buy_i + 1, n):
        if cs[j] < ma20[j]:
            exit_i = j
            break
    if exit_i is None:
        exit_i = n - 1
    pnl = (cs[exit_i] / buy - 1) * 100 - FEE * 100
    return {'status': 'done', 'buy_i': buy_i, 'hold': exit_i - buy_i,
            'pnl': pnl, 'exit': exit_i}


def trade_pullback_c2(cs, lows, ma20, ma60, i):
    """C 组：等回踩+确认日作为信号日，交给 run_trade（次日收盘买+C2 尺）。"""
    n = len(cs)
    touch = None
    for j in range(i + 1, min(i + 1 + WAIT_N, n)):
        if touch is None and lows[j] <= ma20[j]:
            touch = j
        if touch is not None and j >= touch and cs[j] > ma20[j]:
            t = run_trade(cs, ma20, ma60, j)
            if t:
                return {'status': 'done', 'pnl': t['pnl'], 'buy_i': t['buy_i'],
                        'conf': j, 'exit': t['sells'][-1][0]}
            return {'status': 'failed'}
    if touch is None:
        return {'status': 'miss'}
    return {'status': 'failed'}


def trade_direct_c2(cs, ma20, ma60, i):
    """D 组：追买+C2 尺（= 定稿扫描陡上穿档的同款口径）。"""
    t = run_trade(cs, ma20, ma60, i)
    if t:
        return {'status': 'done', 'pnl': t['pnl'], 'exit': t['sells'][-1][0]}
    return {'status': 'eod_open'}


def main():
    all_rows = fetch_full()
    rows = []
    for s in POOL:
        rows += run_sym(s, all_rows[s])
    report(rows)


def fetch_full():
    out = {}
    for s in POOL:
        ks, end = [], ''
        while True:
            url = (f'https://data-api.binance.vision/api/v3/klines?symbol={s}&interval=1d'
                   f'&limit=1000{f"&endTime={end}" if end else ""}')
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                batch = json.loads(r.read())
            if not batch:
                break
            ks = batch + ks
            if len(batch) < 1000:
                break
            end = batch[0][0] - 1
            time.sleep(0.15)
        now_ms = time.time() * 1000
        out[s] = [(time.strftime('%Y-%m-%d', time.gmtime(k[0] / 1000)),
                   float(k[3]), float(k[4])) for k in ks if k[6] < now_ms]  # (day, low, close)
        print(f'{s}: {len(out[s])} 根')
    return out


def run_sym(sym, bars):
    dates = [b[0] for b in bars]
    lows = np.array([b[1] for b in bars])
    cs = np.array([b[2] for b in bars])
    c = pd.Series(cs, dtype=float)
    res = []
    busy_until = -1  # 持仓期间不接受新触发（A/B 共用同信号集合，按 A 计）
    for i, sv, tier, m20a, m60a in steep_crosses(c, dates):
        if i <= busy_until:
            continue
        if i < 65 or dates[i] < '2018-01-01':
            continue
        b = trade_pullback(cs, lows, m20a, i)
        a = trade_direct(cs, m20a, i)
        cc = trade_pullback_c2(cs, lows, m20a, m60a, i)
        d = trade_direct_c2(cs, m20a, m60a, i)
        if a.get('status') == 'done':
            busy_until = a['exit']
        rec = {'sym': sym, 'sig_day': dates[i], 'year': dates[i][:4], 's20': round(sv, 2),
               'tier': tier, 'a_pnl': a.get('pnl'), 'b_status': b['status'],
               'b_pnl': b.get('pnl'), 'c_pnl': cc.get('pnl'), 'c_status': cc['status'],
               'd_pnl': d.get('pnl'),
               'wait': (b.get('buy_i') - i) if b.get('buy_i') else None}
        res.append(rec)
    return res


def _lows_of(*a):  # 未用占位
    return []


def st(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    v = sorted(vals)
    win = sum(1 for x in v if x > 0) / len(v) * 100
    return f'均值{sum(v)/len(v):+7.2f}% 中位{v[len(v)//2]:+7.2f}% 胜率{win:4.1f}%'


def report(rows):
    print(f'\n===== 陡上穿买点对照：A 直接追买 vs B 等回踩 MA20（池 6 币，破 MA20 全清，双边 0.1%）=====')
    done_a = [r for r in rows if r['a_pnl'] is not None]
    done_b = [r for r in rows if r['b_status'] == 'done']
    missed = [r for r in rows if r['b_status'] == 'miss']
    failed = [r for r in rows if r['b_status'] == 'failed']
    print(f'\n陡上穿信号总数 {len(rows)} 笔（s20>0.5）')
    print(f'A 追买: {len(done_a)} 笔  {st([r["a_pnl"] for r in done_a])}')
    nav = 1.0
    for r in done_a:
        nav *= 1 + r['a_pnl'] / 100
    print(f'A 串行净值 {nav:.2f}x  平均持有 {sum(r["a_hold"] for r in done_a)/len(done_a):.0f} 根'
          if False else
          f'A 串行净值 {nav:.2f}x')
    print(f'B 回踩买: 成交 {len(done_b)} 笔  {st([r["b_pnl"] for r in done_b])}')
    navb = 1.0
    for r in done_b:
        navb *= 1 + r['b_pnl'] / 100
    print(f'B 串行净值 {navb:.2f}x')
    print(f'B 未回踩错失 {len(missed)} 笔（A 平均 {st([r["a_pnl"] for r in missed])}——错失成本）')
    print(f'B 回踩未确认 {len(failed)} 笔（A 平均 {st([r["a_pnl"] for r in failed])}）')
    w = [r["wait"] for r in done_b if r["wait"]]
    if w:
        print(f'B 等待天数: 中位 {sorted(w)[len(w)//2]} 均值 {sum(w)/len(w):.1f}')

    print(f'\n----- C/D 组（C2 原版卖出尺：破MA20卖半/破MA60清/trail/硬止损）-----')
    done_c = [r for r in rows if r['c_status'] == 'done']
    done_d = [r for r in rows if r['d_pnl'] is not None]
    navc = 1.0
    for r in done_c:
        navc *= 1 + r['c_pnl'] / 100
    navd = 1.0
    for r in done_d:
        navd *= 1 + r['d_pnl'] / 100
    print(f'C 回踩买+C2尺: {len(done_c)} 笔  {st([r["c_pnl"] for r in done_c])}  净值 {navc:.2f}x')
    print(f'D 追买+C2尺:   {len(done_d)} 笔  {st([r["d_pnl"] for r in done_d])}  净值 {navd:.2f}x')
    miss_c = [r for r in rows if r['c_status'] == 'miss']
    print(f'C 错失 {len(miss_c)} 笔（D 平均 {st([r["d_pnl"] for r in miss_c])}）')

    print(f'\n----- 分档（B 成交笔 / 错失笔的 A 收益）-----')
    for lo, hi, name in TIERS:
        sub = [r for r in rows if r['tier'] == name]
        bd = [r for r in sub if r['b_status'] == 'done']
        bm = [r for r in sub if r['b_status'] == 'miss']
        print(f'{name}({lo}-{hi}): 触发{len(sub)}  B成交{len(bd)} {st([r["b_pnl"] for r in bd])}'
              f'  错失{len(bm)} {st([r["a_pnl"] for r in bm])}')

    print(f'\n----- 分年（B 成交）-----')
    for y in sorted(set(r['year'] for r in done_b)):
        sub = [r for r in done_b if r['year'] == y]
        print(f'{y}: {len(sub)} 笔 {st([r["b_pnl"] for r in sub])}')

    print(f'\n----- 分币（B 成交）-----')
    for s in sorted(set(r['sym'] for r in done_b)):
        sub = [r for r in done_b if r['sym'] == s]
        print(f'{s}: {len(sub)} 笔 {st([r["b_pnl"] for r in sub])}')

    print(f'\n----- 关键个案（发令枪型信号，B 等没等到）-----')
    for r in rows:
        if (r['sym'], r['year']) in [('ETHUSDT', '2020'), ('SOLUSDT', '2023'),
                                     ('BTCUSDT', '2020'), ('ETHUSDT', '2023')]:
            print(f"  {r['sym']} {r['sig_day']} s20={r['s20']} [{r['tier']}]  "
                  f"A追买{r['a_pnl'] if r['a_pnl'] is not None else 'eod'}%  "
                  f"B={r['b_status']}" + (f" {r['b_pnl']:+.1f}%" if r['b_pnl'] else ""))

    json.dump(rows, open(os.path.join(HERE, 'data', 'steep_pullback_rows.json'), 'w'),
              ensure_ascii=False, indent=1)
    print(f'\n明细存 steep_pullback_rows.json')


if __name__ == '__main__':
    main()
