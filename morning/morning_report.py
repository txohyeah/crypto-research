#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto 晨报（2026-09-21 上线）：6 币 × 2 体系每日信号监控 + 飞书推送。

池：BTC/ETH/SOL/BNB/AR/DOGE（2026-09-21 用户拍板，DOGE 正式入池）
体系 A：金叉 v2 定稿（+v3 粘合观察层）——引擎 bt_goldencross_v2 复用，卖出事件逐条提醒
体系 B：超跌起爆（A 股 9/6 口径平移）——引擎 bt_oversold.scan_signals 复用，
        卖出=收盘<max(买价×0.9, 30根低不含当根) 全清（T1 建仓口径）
体系 C：上穿MA60观察铃（B版策略·2026-09-29 上线，当晚改版）——只提醒不建模拟仓：
        收盘上穿 MA60 → 第1天预警（附回测警示文案+MA60斜率）
        → 连续站住第2/3天各推确认 → 收盘跌回 MA60 推作废；
        无冷却（用户 9/29 拍板：通知不是建仓，第一次未必买，每次上穿都报）
        池：BTC/ETH/SOL/BNB/AR/DOGE/UNI 七币（CRV 用户拍板移除）

时间线（币安日线 openTime=UTC 00:00=北京 08:00）：
  北京 D+1 日 08:05 任务运行 → 最新完整 K 线=标签 D（今晨 08:00 刚收）
  信号在其上确认 → 8:05 提醒买入 → 体系 T1 建仓=标签 D+1 那根收盘=北京 D+2 08:00
状态：morning_state.json（seen 信号指纹 + 持仓台账 + 卖出提醒进度）
推送：.secrets/feishu_webhook_url 存在才推；有事件才推，静默日不扰
用法：python morning_report.py [--dry-run]（dry-run 不写 state 不推送）
"""
import json
import os
import sys
import time
import datetime as dt
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bt_goldencross_v2 import signals as gc_signals, run_trade, SYMS as GC_SYMS
from bt_oversold import scan_signals

POOL = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT', 'DOGEUSDT']
B_POOL = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ARUSDT', 'DOGEUSDT', 'UNIUSDT']  # 体系C观察铃七币（2026-09-29 晚：SOL/DOGE 入池）
STATE_F = os.path.join(HERE, 'data', 'morning_state.json')
WEBHOOK_F = '/root/.qwenpaw/workspaces/default/.secrets/feishu_webhook_url'
LARK_ENV = '/root/.qwenpaw/workspaces/default/.secrets/feishu_lark.env'
LARK_CHAT_F = '/root/.qwenpaw/workspaces/default/.secrets/feishu_target_chat'
LARK_OPEN_ID_F = '/root/.qwenpaw/workspaces/default/.secrets/feishu_target_open_id'
BASE = 'https://data-api.binance.vision/api/v3/klines?symbol={}&interval=1d&limit=1000{}'
DRY = '--dry-run' in sys.argv
SYSCN = {'gc_v2': '金叉v2', 'gc_v3': '粘合金叉', 'ovs': '超跌起爆'}


def fetch(sym):
    """全史日线 → [(day, close)]，含 closeTime 用于完整性判定。"""
    out, end = [], ''
    while True:
        url = BASE.format(sym, f'&endTime={end}' if end else '')
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as r:
                    batch = json.loads(r.read())
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
        if not batch:
            break
        out = batch + out
        if len(batch) < 1000:
            break
        end = batch[0][0] - 1
        time.sleep(0.2)
    rows = [(time.strftime('%Y-%m-%d', time.gmtime(k[0] / 1000)), float(k[4]), k[6]) for k in out]
    return rows


def load_data(symbols=None):
    data, closed = {}, {}
    for s in (symbols or POOL):
        rows = fetch(s)
        now_ms = time.time() * 1000
        full = [r for r in rows if r[2] < now_ms]          # 只保留已收盘的
        data[s] = [r[0] for r in full]
        closed[s] = np.array([r[1] for r in full])
    return data, closed


def gc_v3_events(c, dates):
    """v3 粘合金叉（θ=2.0 定稿）：无穿越事件、与 v2 天然互斥。"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    s20 = ma20.diff() / ma20.shift(1) * 100
    gap = (ma20 - ma60) / ma60 * 100
    no_die = (ma20 - ma60).rolling(60, min_periods=60).min() >= 0
    pin = gap.rolling(15, min_periods=1).min() <= 2.0
    rev = (gap > gap.shift(1)) & (gap.shift(1) > gap.shift(2)) & (s20 > 0) & (s20 <= 0.5) & (gap <= 2.0)
    idxs = np.flatnonzero((no_die & pin & rev).to_numpy())
    ev, last = [], -10**9
    for i in idxs:
        if i - last < 10:
            continue
        last = i
        ev.append(int(i))
    return ev


def ovs_events(c, dates):
    """超跌起爆事件（复用 bt_oversold.scan_signals）。"""
    rows = [(d, 0.0, 0.0, 0.0, float(x), 0.0) for d, x in zip(dates, c)]
    sigs, _, _ = scan_signals(rows)
    return [i for i, _ in sigs]


def ovs_sell_events(closes, i):
    """超跌 T1 卖出回放：次日收盘买，stop=max(买价×0.9, 30根低不含当根)。返回 (buy_i, events)。"""
    n = len(closes)
    buy_i = i + 1
    if buy_i >= n:
        return None, []
    buy = closes[buy_i]
    events = []
    for j in range(buy_i + 1, n):
        lo30 = min(closes[max(0, j - 30):j])
        if closes[j] < max(buy * 0.9, lo30):
            events.append((j, 'stop'))
            return buy_i, events
    events.append((n - 1, 'eod'))
    return buy_i, events


def build_universe(data, closes):
    """全史回放：每体系的全部信号+卖出事件。返回 [{key,sys,sym,sig_day,sig_i,buy_i,buy_day,events[...]}]"""
    uni = []
    for s in POOL:
        dates = data[s]
        cs = closes[s]
        c = pd.Series(cs, dtype=float)
        ev, ma20, ma60 = gc_signals(c, dates)
        m20, m60 = ma20, ma60
        for i in ev:
            t = run_trade(cs, m20, m60, i)
            if t:
                uni.append({'key': f'gc_v2|{s}|{dates[i]}', 'sys': 'gc_v2', 'sym': s,
                            'sig_day': dates[i], 'sig_i': i, 'buy_i': t['buy_i'],
                            'buy_day': dates[t['buy_i']], 'events': t['sells'],
                            'open': t['sells'][-1][1] == 'eod' if t['sells'] else True})
        for i in gc_v3_events(c, dates):
            t = run_trade(cs, m20, m60, i)
            if t:
                uni.append({'key': f'gc_v3|{s}|{dates[i]}', 'sys': 'gc_v3', 'sym': s,
                            'sig_day': dates[i], 'sig_i': i, 'buy_i': t['buy_i'],
                            'buy_day': dates[t['buy_i']], 'events': t['sells'],
                            'open': t['sells'][-1][1] == 'eod' if t['sells'] else True})
        for i in ovs_events(c, dates):
            buy_i, evs = ovs_sell_events(cs, i)
            if buy_i is not None:
                uni.append({'key': f'ovs|{s}|{dates[i]}', 'sys': 'ovs', 'sym': s,
                            'sig_day': dates[i], 'sig_i': i, 'buy_i': buy_i,
                            'buy_day': dates[buy_i], 'events': evs,
                            'open': evs[-1][1] == 'eod' if evs else True})
    return uni


# --- 体系 C：上穿MA60观察铃（B版·2026-09-29 拍板，只提醒不建仓） ---

def b_cross_watch(data, closes, watch):
    """收盘价上穿 MA60 三段式观察铃：第1天预警 → 连续站住第2/3天确认 → 收盘跌回作废。
    判据与 bt_price_cross 回测同口径（收盘 vs 当日 MA60，rolling 含当日，i>=60 均线成型）。
    无冷却：每次上穿都提醒（用户 2026-09-29 拍板——通知不是建仓，第一次未必买）。
    watch: {sym: {'start': day, 'days': n}}。返回提醒文案列表。"""
    alerts = []
    for s in B_POOL:
        dates, cs = data[s], closes[s]
        ma60 = pd.Series(cs, dtype=float).rolling(60).mean().to_numpy()
        i = len(cs) - 1
        if i < 60 or not (np.isfinite(ma60[i]) and np.isfinite(ma60[i - 1])):
            continue
        px, mav, prev = cs[i], ma60[i], cs[i - 1]
        above_now, above_prev = px > mav, prev > ma60[i - 1]
        dist = (px / mav - 1) * 100
        s60 = (mav / ma60[i - 1] - 1) * 100
        day = dates[i]
        w = watch.get(s)
        if w:
            if above_now:
                w['days'] += 1
                if w['days'] == 2:
                    alerts.append(f"🟡 上穿MA60·站住第2天｜{s}：今晨收盘 {px:.4g}"
                                  f"（距 MA60 {mav:.4g} +{dist:.1f}%）。若你的 N=2，今天是确认日。")
                elif w['days'] >= 3:
                    alerts.append(f"🟢 上穿MA60·站住第3天｜{s}：今晨收盘 {px:.4g}"
                                  f"（距 MA60 +{dist:.1f}%）。若你的 N=3，今天是确认日。观察完毕，本次上穿不再跟踪。")
                    watch.pop(s)
            else:
                label = '次日即回落' if w['days'] == 1 else f'站住 {w["days"]} 天后回落'
                alerts.append(f"⚫ 上穿MA60·回落作废｜{s}：今晨收盘 {px:.4g} 跌回 MA60"
                              f"（{mav:.4g}）下方，{label}，观察终止。")
                watch.pop(s)
        else:
            if not (above_now and not above_prev):
                continue
            watch[s] = {'start': day, 'days': 1}
            alerts.append(f"🟡 上穿MA60·第1天预警｜{s}：今晨收盘 {px:.4g} 上穿 MA60"
                          f"（{mav:.4g}，距 +{dist:.1f}%，MA60斜率 {s60:+.2f}%/根）。"
                          f"⚠️ 回测警示：当天追入是最差执行档（CRV 6年净值0.38x/胜率18%），"
                          f"等连续站住2~3天再入的典型收益显著更优；站住第几天由你定——站住/回落我会再报。")
    return alerts


def main():
    all_syms = list(dict.fromkeys(POOL + B_POOL))
    data, closes = load_data(all_syms)
    uni = build_universe(data, closes)
    last_day = {s: data[s][-1] for s in POOL}
    now = time.strftime('%Y-%m-%d %H:%M', time.localtime())

    state = {'version': 1, 'seen': [], 'positions': {}}
    if os.path.exists(STATE_F):
        state = json.load(open(STATE_F))
    seen = set(state['seen'])
    pos = state['positions']
    b_watch = state.setdefault('b_watch', {})
    first_run = not state['seen'] and not pos

    buys, sells, notes = [], [], []
    def _dminus(day_str, n):
        t = time.strptime(day_str, '%Y-%m-%d')
        return (dt.date(t.tm_year, t.tm_mon, t.tm_mday) - dt.timedelta(days=n)).isoformat()
    for u in uni:
        key = u['key']
        is_new = key not in seen
        # 首跑 seeding：信号日早于最新K线的全部视为历史，静默登记；
        # 其中"数据末尾仍未离场"的登记为在监持仓（历史卖出事件不补发提醒）
        if first_run and u['sig_day'] < last_day[u['sym']]:
            seen.add(key)
            if u['open'] and key not in pos:
                n_hist = len([1 for t, tp in u['events'] if tp != 'eod'])
                pos[key] = {'sys': u['sys'], 'sym': u['sym'], 'sig_day': u['sig_day'],
                            'buy_day': u['buy_day'], 'open': True, 'sells_alerted': n_hist,
                            'legacy': True}
                notes.append(f"已登记历史遗留持仓：{SYSCN[u['sys']]}·{u['sym']}（信号日 {u['sig_day']}）")
            continue
        non_eod = [(t, tp) for t, tp in u['events'] if tp != 'eod']
        # --- 新买入信号（今晨 08:00 收盘确认） ---
        if is_new and u['sig_day'] == last_day[u['sym']]:
            buys.append(u)
            seen.add(key)
            if key not in pos:
                pos[key] = {'sys': u['sys'], 'sym': u['sym'], 'sig_day': u['sig_day'],
                            'buy_day': u['buy_day'], 'open': True, 'sells_alerted': 0}
        elif is_new and not first_run and u['sig_day'] == _dminus(last_day[u['sym']], 1):
            # cron 补跑：昨日信号漏提醒，但 T1 建仓窗（今日收盘=明早 08:00）未过
            buys.append(u)
            seen.add(key)
            if key not in pos:
                pos[key] = {'sys': u['sys'], 'sym': u['sym'], 'sig_day': u['sig_day'],
                            'buy_day': u['buy_day'], 'open': True, 'sells_alerted': 0}
            notes.append('⚠️ 上条为昨日信号补提醒（昨日任务未跑）')
        # --- 持仓卖出事件提醒（金叉：逐事件；超跌：stop 单事件） ---
        if key in pos and pos[key].get('open', True):
            alerted = pos[key]['sells_alerted']
            fresh = non_eod[len(non_eod) - (len(non_eod) - alerted):] if len(non_eod) > alerted else []
            for t, tp in fresh:
                if tp == 'half_ma20':
                    half = True
                    px = closes[u['sym']][t]
                    gain = (px / closes[u['sym']][u['buy_i']] - 1) * 100
                    sells.append(f"{SYSCN[u['sys']]}｜{u['sym']} 信号仓（信号日 {u['sig_day']}）："
                                 f"跌破 MA20 → 卖出一半 @收盘 {px:.4g}（该半仓浮动 {gain:+.1f}%）；"
                                 f"剩余仓位破 MA60 清仓")
                else:
                    px = closes[u['sym']][t]
                    gain = (px / closes[u['sym']][u['buy_i']] - 1) * 100
                    why = {'stop': '触发硬止损', 'ma60': '跌破 MA60', 'trail': '移动止盈回吐 1/3'}[tp]
                    sells.append(f"{SYSCN[u['sys']]}｜{u['sym']} 信号仓（信号日 {u['sig_day']}）："
                                 f"{why} → 全部清仓 @收盘 {px:.4g}（全仓口径 {gain:+.1f}%）")
                pos[key]['sells_alerted'] += 1
                if tp in ('stop', 'ma60', 'trail'):
                    pos[key]['open'] = False
                    pos[key]['closed_day'] = data[u['sym']][t]

    # --- 手工仓结构位监控（无成本价，只报 MA20/MA60 破位，收复后重置） ---
    manual = state.setdefault('manual_positions', {})
    for sym, m in manual.items():
        if sym not in closes:
            continue
        c = pd.Series(closes[sym], dtype=float)
        ma20v, ma60v = float(c.rolling(20).mean().iloc[-1]), float(c.rolling(60).mean().iloc[-1])
        pxv = float(closes[sym][-1])
        if m.get('above_ma20', True) and pxv < ma20v:
            sells.append(f"手工仓｜{sym}（剩 {m.get('pct','?')}）：收盘跌破 MA20（{ma20v:.4g}）"
                         f"@{pxv:.4g}，结构转弱——按 C2 口径提示减仓")
            m['above_ma20'] = False
        elif not m.get('above_ma20', True) and pxv > ma20v:
            m['above_ma20'] = True          # 收复，重置（下次跌破再报）
        if m.get('above_ma60', True) and pxv < ma60v:
            sells.append(f"手工仓｜{sym}：收盘跌破 MA60（{ma60v:.4g}）@{pxv:.4g}——C2 清仓位")
            m['above_ma60'] = False
        elif not m.get('above_ma60', True) and pxv > ma60v:
            m['above_ma60'] = True

    # --- 体系C观察铃（B版）：基于最新收盘 K 线的三段式提醒 ---
    b_alerts = b_cross_watch(data, closes, b_watch)

    # 持仓概览
    open_pos = [p for p in pos.values() if p.get('open')]
    manual_open = [f"{s}(手工{m['pct']})" for s, m in manual.items()]
    lines = [f'📈 Crypto 晨报 {now}', '━━━━━━━━━━━━━━']
    if buys:
        lines.append('🔔 买入信号（今晨 08:00 收盘确认）')
        for u in buys:
            extra = ''
            if u['sys'] in ('gc_v2', 'gc_v3'):
                c = pd.Series(closes[u['sym']], dtype=float)
                ma20 = c.rolling(20).mean().iloc[u['sig_i']]
                ma60 = c.rolling(60).mean().iloc[u['sig_i']]
                extra = f'（MA20 {ma20:.4g} / MA60 {ma60:.4g}）'
            lines.append(f"• {SYSCN[u['sys']]}｜{u['sym']}：信号日 {u['sig_day']}，"
                         f"体系 T1 建仓=下一根日线收盘（明早 08:00）{extra}")
    if b_alerts:
        lines.append('')
        lines.append('🔎 上穿MA60观察铃（只提醒·不建仓，N 由你定）')
        lines.extend(f'• {x}' for x in b_alerts)
    if sells:
        lines.append('')
        lines.append('🔻 卖出提醒（基于昨晚收盘）')
        lines.extend(f'• {x}' for x in sells)
    lines.append('')
    if open_pos or manual_open:
        parts = [f"{SYSCN[p['sys']]}·{p['sym']}({p['sig_day']})" for p in open_pos] + manual_open
        lines.append(f"📌 在监持仓 {len(open_pos) + len(manual_open)} 笔：" + '、'.join(parts))
    else:
        lines.append('📌 在监持仓 0 笔')
    fresh_cnt = len(buys) + len(sells) + len(b_alerts)
    if fresh_cnt == 0:
        lines.append('今日无新信号，一切照旧')

    print('\n'.join(lines))

    if DRY:
        print('\n[dry-run] state 未写入、未推送')
        return
    # 心跳推送：有信号必推（事件有台账对账天然幂等）；无信号日每天也推一条确认，
    # 用户"每天收到=系统活着"，收不到即知道链路坏了；state.last_push 保证当天补跑不重发
    today = dt.datetime.now().strftime('%Y-%m-%d')
    need_push = fresh_cnt > 0 or state.get('last_push') != today
    if need_push:
        state['last_push'] = today
    state['seen'] = sorted(seen)
    state['positions'] = pos
    state['b_watch'] = b_watch
    json.dump(state, open(STATE_F, 'w'), ensure_ascii=False, indent=1)
    if need_push:
        push_feishu('\n'.join(lines))


def push_feishu(text):
    """通道1：自定义机器人 webhook；通道2：自建应用+目标 chat_id。"""
    if os.path.exists(WEBHOOK_F):
        url = open(WEBHOOK_F).read().strip()
        msg = {'msg_type': 'text', 'content': {'text': text}}
        req = urllib.request.Request(url, data=json.dumps(msg).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            ok = resp.get('code') == 0 or resp.get('StatusCode') == 0
            print(f'[push] 飞书(webhook) {"成功" if ok else resp}')
        except Exception as e:
            print(f'[push] 飞书推送失败: {e}')
        return
    if os.path.exists(LARK_ENV) and os.path.exists(LARK_CHAT_F):
        env = {}
        for line in open(LARK_ENV):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k] = v
        chat_id = open(LARK_CHAT_F).read().strip()
        try:
            req = urllib.request.Request(
                'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                data=json.dumps({'app_id': env['LARK_APP_ID'],
                                 'app_secret': env['LARK_APP_SECRET']}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as r:
                tok = json.loads(r.read())
            if tok.get('code') != 0:
                print(f'[push] lark token 失败 {tok.get("code")} {tok.get("msg")}')
                return
            msg = {'receive_id': chat_id, 'msg_type': 'text',
                   'content': json.dumps({'text': text})}
            req2 = urllib.request.Request(
                'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
                data=json.dumps(msg).encode(),
                headers={'Content-Type': 'application/json',
                         'Authorization': f"Bearer {tok['tenant_access_token']}"})
            with urllib.request.urlopen(req2, timeout=15) as r:
                resp = json.loads(r.read())
            ok = resp.get('code') == 0
            detail = '成功' if ok else f"失败 {resp.get('code')} {resp.get('msg')}"
            print(f'[push] 飞书(应用→chat) {detail}')
        except Exception as e:
            print(f'[push] 飞书推送失败: {e}')
        return
    if os.path.exists(LARK_ENV) and os.path.exists(LARK_OPEN_ID_F):
        env = {}
        for line in open(LARK_ENV):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k] = v
        open_id = open(LARK_OPEN_ID_F).read().strip()
        try:
            req = urllib.request.Request(
                'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                data=json.dumps({'app_id': env['LARK_APP_ID'],
                                 'app_secret': env['LARK_APP_SECRET']}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as r:
                tok = json.loads(r.read())
            if tok.get('code') != 0:
                print(f'[push] lark token 失败 {tok.get("code")} {tok.get("msg")}')
                return
            msg = {'receive_id': open_id, 'msg_type': 'text',
                   'content': json.dumps({'text': text})}
            req2 = urllib.request.Request(
                'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
                data=json.dumps(msg).encode(),
                headers={'Content-Type': 'application/json',
                         'Authorization': f"Bearer {tok['tenant_access_token']}"})
            with urllib.request.urlopen(req2, timeout=15) as r:
                resp = json.loads(r.read())
            ok = resp.get('code') == 0
            detail = '成功' if ok else f"失败 {resp.get('code')} {resp.get('msg')}"
            print(f'[push] 飞书(应用→单聊) {detail}')
        except Exception as e:
            print(f'[push] 飞书推送失败: {e}')
        return
    print('[push] 未配置推送通道（webhook / feishu_target_chat / feishu_target_open_id），仅输出未推送')


if __name__ == '__main__':
    main()
