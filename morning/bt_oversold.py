#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto 超跌起爆回测：拉取 + 回测一体。

口径（2026-09-20 与用户对齐）：
- 标的池：Binance 现货主流 26 币（幸存者偏差声明：全是 2026 年还活着的币，结果偏乐观）
- 买入信号（超跌起爆四条件，A 股 9/6 定稿口径平移）：
  1) RSI6 TDX 口径自下穿上 40（昨天<40 且 今天>=40）
  2) 收盘距 60 日滚动最高收盘 <= -30%
  3) 已离底：比 60 日滚动最低收盘高 >= 5%
  4) 弹回比例 <= 75%：(c - lo60)/(hi60 - lo60) <= 0.75
  冷却：同币 10 根内不重复触发
- 建仓两变体：
  T0（A 股主口径平移）：信号日收盘买；信号日涨幅 > 5% 则顺延次日收盘买
  T1（crypto 保守版）：信号次日收盘买
- 卖出（简化 C2，无金牛上沿）：收盘 < max(买价*0.9, 买入后滚动 30 根最低收盘不含当根) 全清
- 费用双边 0.1%；指标：f20 均值/中位/胜率、MAE20、C2 每笔收益/持有天数/串行净值
- 全指标只往回看（因果），无金牛线类居中平滑
"""
import json
import os
import urllib.request
import datetime as dt

PROXY = 'http://127.0.0.1:7890'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': PROXY, 'http': PROXY}))
BASE = 'https://api.binance.com/api/v3/klines'
CACHE = os.path.join(os.path.dirname(__file__), 'data', 'klines_cache.json')

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT',
           'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'ATOMUSDT', 'NEARUSDT',
           'UNIUSDT', 'AAVEUSDT', 'FILUSDT', 'ETCUSDT', 'XLMUSDT', 'INJUSDT', 'OPUSDT',
           'ARBUSDT', 'APTUSDT', 'SUIUSDT', 'TONUSDT', 'ARUSDT']


def fetch_klines(sym):
    """翻页拉全历史日线，返回 [(day, open, high, low, close, volume), ...]"""
    out, start = [], 0
    while True:
        url = f'{BASE}?symbol={sym}&interval=1d&limit=1000&startTime={start}'
        with opener.open(url, timeout=30) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        out += batch
        start = batch[-1][0] + 86400000
        if len(batch) < 1000:
            break
    rows = []
    for k in out:
        day = dt.datetime.fromtimestamp(k[0] / 1000, dt.timezone.utc).strftime('%Y-%m-%d')
        rows.append((day, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])))
    # 去重保序
    seen, dedup = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            dedup.append(r)
    return dedup


def load_all():
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
        print(f'缓存命中 {len(cache)} 币')
    changed = False
    for sym in SYMBOLS:
        if sym in cache:
            continue
        try:
            rows = fetch_klines(sym)
            cache[sym] = rows
            changed = True
            print(f'{sym}: {len(rows)} 根 ({rows[0][0]} ~ {rows[-1][0]})')
        except Exception as e:
            print(f'{sym}: 拉取失败 {e}')
    if changed:
        json.dump(cache, open(CACHE, 'w'))
        print(f'缓存已写 {CACHE}')
    return cache


# ---------------- 指标（全部因果：只往回看） ----------------

def rsi_tdx(closes, n=6):
    """TDX RSI：SMA(MAX(C,REF(C,1),0),N,1)/SMA(ABS(C-REF(C,1)),N,1)*100，SMA 递推 y=(x+(N-1)*y')/N"""
    out = [None] * len(closes)
    up = dn = None
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        up = (max(ch, 0) + (n - 1) * up) / n if up is not None else max(ch, 0)
        dn = (abs(ch) + (n - 1) * dn) / n if dn is not None else abs(ch)
        out[i] = 100.0 * up / dn if dn else 50.0
    return out


def roll_max(xs, n):
    out = [None] * len(xs)
    for i in range(len(xs)):
        if i >= n:
            out[i] = max(xs[i - n:i])
    return out


def roll_min(xs, n):
    out = [None] * len(xs)
    for i in range(len(xs)):
        if i >= n:
            out[i] = min(xs[i - n:i])
    return out


def scan_signals(rows):
    """返回信号列表 [(idx, 四条件快照)]"""
    days = [r[0] for r in rows]
    closes = [r[4] for r in rows]
    rsi = rsi_tdx(closes)
    hi60 = roll_max(closes, 60)
    lo60 = roll_min(closes, 60)
    sigs, last_sig = [], -999
    for i in range(1, len(closes)):
        if hi60[i] is None or rsi[i] is None or rsi[i - 1] is None:
            continue
        if not (rsi[i - 1] < 40 <= rsi[i]):
            continue
        if i - last_sig < 10:
            continue
        drop = closes[i] / hi60[i] - 1.0          # 条件2：<= -30%
        off_low = closes[i] / lo60[i] - 1.0        # 条件3：>= 5%
        rng = hi60[i] - lo60[i]
        bounce = (closes[i] - lo60[i]) / rng if rng > 0 else 1.0  # 条件4：<= 0.75
        if drop <= -0.30 and off_low >= 0.05 and bounce <= 0.75:
            sigs.append((i, {'drop': round(drop * 100, 1), 'off_low': round(off_low * 100, 1),
                             'bounce': round(bounce * 100, 1), 'rsi6': round(rsi[i], 1)}))
            last_sig = i
    return sigs, days, closes


def run_backtest(cache):
    # ---------- 事件研究：f20 / MAE20（T0、T1 两口径） ----------
    ev_rows = []   # 事件研究行
    tr_rows = []   # C2 推进行（T1 主口径）
    for sym, rows in sorted(cache.items()):
        if len(rows) < 80:
            continue
        sigs, days, closes = scan_signals(rows)
        for idx, snap in sigs:
            sig_day, sig_close = days[idx], closes[idx]
            sig_chg = (closes[idx] / closes[idx - 1] - 1) * 100 if idx > 0 else 0.0
            # --- 事件研究 f20：T0（信号日买，+5% 顺延）/ T1（次日买） ---
            def fwd(entry_i):
                if entry_i >= len(closes):
                    return None, None
                entry = closes[entry_i]
                f20_i = min(entry_i + 20, len(closes) - 1)
                f20 = (closes[f20_i] / entry - 1) * 100
                mae = min((closes[j] / entry - 1) * 100 for j in range(entry_i, f20_i + 1))
                return f20, mae
            # T0：默认信号日买，涨幅>5% 顺延
            t0_i = idx + 1 if sig_chg > 5 else idx
            t0_f20, t0_mae = fwd(t0_i) if t0_i < len(closes) else (None, None)
            t1_f20, t1_mae = fwd(idx + 1) if idx + 1 < len(closes) else (None, None)
            ev_rows.append({'sym': sym, 'sig_day': sig_day, 'sig_chg': round(sig_chg, 1),
                            **{f's_{k}': v for k, v in snap.items()},
                            't0_buy_day': days[t0_i] if t0_i < len(days) else '',
                            't0_f20': t0_f20, 't0_mae': t0_mae,
                            't1_buy_day': days[idx + 1] if idx + 1 < len(days) else '',
                            't1_f20': t1_f20, 't1_mae': t1_mae})
            # --- C2 推进（T1 口径：次日收盘买，无条件——>5% 不追是 T0 专属规则） ---
            if idx + 1 >= len(closes):
                continue
            buy_i = idx + 1
            buy = closes[buy_i]
            stop = buy * 0.9
            exit_i, exit_px, reason = None, None, ''
            for j in range(buy_i + 1, len(closes)):
                # 滚动 30 根最低不含当根：全局窗口 [j-30, j)，含买入根——
                # 不能从买入根起切（那样初期止损线=买价附近，必然秒止损）
                lo30 = min(closes[max(0, j - 30):j])
                stop = max(buy * 0.9, lo30)
                if closes[j] < stop:
                    exit_i, exit_px, reason = j, closes[j], 'stop'
                    break
            if exit_i is None:
                exit_i, exit_px, reason = len(closes) - 1, closes[-1], 'eod'  # 数据末尾仍未止损
            pnl = (exit_px / buy - 1) * 100 - 0.2  # 双边 0.1%
            tr_rows.append({'sym': sym, 'sig_day': sig_day, 'buy_day': days[buy_i],
                            'exit_day': days[exit_i], 'hold': exit_i - buy_i,
                            'pnl': pnl, 'reason': reason})
    return ev_rows, tr_rows


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 'n=0'
    vals2 = sorted(vals)
    med = vals2[len(vals2) // 2]
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    return f'n={len(vals)} 均值{sum(vals)/len(vals):+.2f}% 中位{med:+.2f}% 胜率{win:.1f}% 最差{min(vals):+.1f}%'


def main():
    cache = load_all()
    ev, tr = run_backtest(cache)
    outdir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(outdir, 'signals.csv'), 'w') as f:
        f.write(','.join(ev[0].keys()) + '\n' if ev else 'empty\n')
        for r in ev:
            f.write(','.join(str(r[k]) for k in ev[0].keys()) + '\n')
    with open(os.path.join(outdir, 'trades.csv'), 'w') as f:
        if tr:
            f.write(','.join(tr[0].keys()) + '\n')
            for r in tr:
                f.write(','.join(str(r[k]) for k in tr[0].keys()) + '\n')

    print(f'\n===== 事件研究（f20 = 信号后 20 个自然交易日收益，MAE = 期内最大不利）=====')
    print(f'【T1 次日收盘买】全部: {stats([r["t1_f20"] for r in ev])}')
    print(f'【T1 MAE】        全部: {stats([r["t1_mae"] for r in ev])}')
    print(f'【T0 当日买(>5%顺延)】全部: {stats([r["t0_f20"] for r in ev])}')
    print(f'\n分币（T1 f20）:')
    for sym in sorted(set(r['sym'] for r in ev)):
        sub = [r for r in ev if r['sym'] == sym]
        print(f'  {sym:9s} {stats([r["t1_f20"] for r in sub])}')
    print(f'\n===== C2 卖出推进（T1 口径，双边 0.1% 费用，简化止损 max(-10%, 滚动30低)）=====')
    print(f'全部: {stats([r["pnl"] for r in tr])}  平均持有 {sum(r["hold"] for r in tr)/max(len(tr),1):.0f} 根')
    nav, wins = 1.0, 0
    for r in tr:
        nav *= 1 + r['pnl'] / 100
        wins += r['pnl'] > 0
    print(f'串行等权净值: {nav:.2f}x  ({wins}/{len(tr)} 笔盈利)')
    print(f'出场原因: ' + ', '.join(f'{k}={v}' for k, v in __import__("collections").Counter(r["reason"] for r in tr).items()))
    print(f'\n分币（C2 每笔）:')
    for sym in sorted(set(r['sym'] for r in tr)):
        sub = [r for r in tr if r['sym'] == sym]
        nav1 = 1.0
        for r in sub:
            nav1 *= 1 + r['pnl'] / 100
        print(f'  {sym:9s} {stats([r["pnl"] for r in sub])}  净值{nav1:.2f}x')
    print(f'\n明细见 {outdir}/signals.csv, trades.csv')


if __name__ == '__main__':
    main()
