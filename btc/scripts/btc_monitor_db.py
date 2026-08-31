#!/usr/bin/env python3
"""BTC 信号监控 - 数据库初始化与操作"""

import sqlite3
import os
import json
from datetime import datetime, date, timedelta
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "btc_monitor.db"


def get_conn():
    """获取数据库连接"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_conn()
    cursor = conn.cursor()

    # 每日指标数据（主表）
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_metrics (
        date TEXT PRIMARY KEY,
        price_usd REAL,
        mvrv REAL,
        fear_greed_value INTEGER,
        fear_greed_label TEXT,
        etf_net_flow_m REAL,           -- 单日净流入（百万美元）
        etf_total_aum_b REAL,          -- 总资产净值（十亿美元）
        funding_rate REAL,             -- 资金费率
        open_interest REAL,            -- 未平仓量（BTC）
        exchange_flow_in REAL,         -- 交易所流入（BTC）
        exchange_flow_out REAL,        -- 交易所流出（BTC）
        spot_volume REAL,              -- 现货交易量（USD），链上交易量
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 确保 spot_volume 字段存在（兼容旧数据库）
    try:
        cursor.execute("ALTER TABLE daily_metrics ADD COLUMN spot_volume REAL")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # ---- 见顶系统 v2 新增链上指标列（bitcoin-data.com，2026-08-23）----
    for col in ("mvrv_z", "sopr", "puell", "nupl", "reserve_risk", "realized_profit"):
        try:
            cursor.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass  # 字段已存在

    # 见顶清单状态（每日快照；detail_json 存六维完整判定）
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS top_checklist (
        date TEXT PRIMARY KEY,
        signals_overheat INTEGER DEFAULT 0,   -- ⚠️ 过热维度数
        signals_turn INTEGER DEFAULT 0,       -- 🔴 转折维度数
        hard_turn INTEGER DEFAULT 0,          -- 资金流/杠杆类转折数（触发硬条件必需）
        level TEXT DEFAULT 'normal',          -- normal | region | confirm
        action TEXT DEFAULT '',
        detail_json TEXT,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 筑底清单状态（每日快照）
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS checklist_status (
        date TEXT PRIMARY KEY,
        signals_met INTEGER DEFAULT 0,
        mvrv_status INTEGER DEFAULT 0,
        mvrv_value REAL,
        fear_greed_status INTEGER DEFAULT 0,
        fear_greed_value INTEGER,
        etf_inflow_days INTEGER DEFAULT 0,
        spot_perp_days INTEGER DEFAULT 0,
        second_test_status TEXT DEFAULT '未回测',
        time_window_status INTEGER DEFAULT 0,
        action TEXT DEFAULT '观望',
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 信号触发日志
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signal_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        signal_type TEXT,
        signal_desc TEXT,
        value REAL,
        threshold REAL,
        triggered INTEGER,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 关键位（前低等）：auto 由 swing-low 算法每日维护，manual 人工设置且优先级更高
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS key_levels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level_type TEXT DEFAULT 'support',
        level_value REAL NOT NULL,
        source TEXT NOT NULL,              -- 'auto' | 'manual'
        anchor_date TEXT,                  -- 该低点形成的日期
        note TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 数据采集日志
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS collect_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        source TEXT,
        status TEXT,
        message TEXT,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    conn.commit()
    conn.close()
    print(f"✅ 数据库初始化完成: {DB_PATH}")


def upsert_daily_metrics(data: dict):
    """插入或更新每日指标"""
    conn = get_conn()
    cursor = conn.cursor()

    # 确保所有字段都有值（None 用于缺失数据）
    fields = {
        "date": data["date"],
        "price_usd": data.get("price_usd"),
        "mvrv": data.get("mvrv"),
        "fear_greed_value": data.get("fear_greed_value"),
        "fear_greed_label": data.get("fear_greed_label"),
        "etf_net_flow_m": data.get("etf_net_flow_m"),
        "etf_total_aum_b": data.get("etf_total_aum_b"),
        "funding_rate": data.get("funding_rate"),
        "open_interest": data.get("open_interest"),
        "exchange_flow_in": data.get("exchange_flow_in"),
        "exchange_flow_out": data.get("exchange_flow_out"),
        "spot_volume": data.get("spot_volume"),
        # 见顶系统 v2 链上指标
        "mvrv_z": data.get("mvrv_z"),
        "sopr": data.get("sopr"),
        "puell": data.get("puell"),
        "nupl": data.get("nupl"),
        "reserve_risk": data.get("reserve_risk"),
        "realized_profit": data.get("realized_profit"),
    }

    cursor.execute("""
    INSERT INTO daily_metrics (date, price_usd, mvrv, fear_greed_value, fear_greed_label,
                               etf_net_flow_m, etf_total_aum_b, funding_rate, open_interest,
                               exchange_flow_in, exchange_flow_out, spot_volume,
                               mvrv_z, sopr, puell, nupl, reserve_risk, realized_profit)
    VALUES (:date, :price_usd, :mvrv, :fear_greed_value, :fear_greed_label,
            :etf_net_flow_m, :etf_total_aum_b, :funding_rate, :open_interest,
            :exchange_flow_in, :exchange_flow_out, :spot_volume,
            :mvrv_z, :sopr, :puell, :nupl, :reserve_risk, :realized_profit)
    ON CONFLICT(date) DO UPDATE SET
        price_usd = COALESCE(excluded.price_usd, price_usd),
        mvrv = COALESCE(excluded.mvrv, mvrv),
        fear_greed_value = COALESCE(excluded.fear_greed_value, fear_greed_value),
        fear_greed_label = COALESCE(excluded.fear_greed_label, fear_greed_label),
        etf_net_flow_m = COALESCE(excluded.etf_net_flow_m, etf_net_flow_m),
        etf_total_aum_b = COALESCE(excluded.etf_total_aum_b, etf_total_aum_b),
        funding_rate = COALESCE(excluded.funding_rate, funding_rate),
        open_interest = COALESCE(excluded.open_interest, open_interest),
        exchange_flow_in = COALESCE(excluded.exchange_flow_in, exchange_flow_in),
        exchange_flow_out = COALESCE(excluded.exchange_flow_out, exchange_flow_out),
        spot_volume = COALESCE(excluded.spot_volume, spot_volume),
        mvrv_z = COALESCE(excluded.mvrv_z, mvrv_z),
        sopr = COALESCE(excluded.sopr, sopr),
        puell = COALESCE(excluded.puell, puell),
        nupl = COALESCE(excluded.nupl, nupl),
        reserve_risk = COALESCE(excluded.reserve_risk, reserve_risk),
        realized_profit = COALESCE(excluded.realized_profit, realized_profit)
    """, fields)

    conn.commit()
    conn.close()


def get_recent_metrics(days: int = 30) -> list:
    """获取最近 N 天的指标"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM daily_metrics
    ORDER BY date DESC
    LIMIT ?
    """, (days,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_latest_metrics() -> dict | None:
    """获取最新一天的指标"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM daily_metrics ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# 见顶清单 v2（六维度三态模型，2026-08-23 与晓道友定稿）
#   每维度三态: green 🟢正常 / warn ⚠️过热 / turn 🔴转折 / None 数据不足
#   触发规则: ⚠️≥2 维过热→停加仓；🔴≥2 维转折且含资金流/杠杆硬信号→执行离场
#   慢变量(估值/情绪)单独永不触发行动，只负责让硬信号响起时敢拿住
# ============================================================
TOP_WINDOW = 400            # 链上指标回看窗口（天）
# MVRV-Z 定标说明（2026-08-24 同源回测）：bitcoin-data.com 口径与 Glassnode 不同，
# 全史窗口(1460天) max=3.35 @2024-12-16 牛初过热，顶日(2025-10-06)仅 2.53 —— 顶部背离逐轮走低。
# Glassnode 经典口径的"双顶 7-9"跨源不可比，严禁混用。
MVRV_Z_WARN = 3.0           # ⚠️ MVRV Z-Score 警戒线（同源实测：≥3 仅出现在牛初估值过热段）
MVRV_Z_PEAK_MIN = MVRV_Z_WARN  # 🔴 峰值有效性保护：峰值曾进过警戒区后的回落才认顶转（防低位噪声；与 WARN 联动）
MVRV_Z_TURN_DROP = 0.35     # 🔴 从峰值回落 ≥35%
NUPL_WARN = 0.75            # ⚠️ NUPL ≥0.75（Euphoria 区）
NUPL_TURN_BACK = 0.65       # 🔴 从 Euphoria 跌回 <0.65
FG_WARN = 85                # ⚠️ 恐惧贪婪 ≥85
FG_STREAK_DAYS = 7          #    连续 ≥7 天
FG_CRASH_DROP = 25          # 🔴 FG 从峰值暴跌 ≥25 点
FG_PEAK_MIN = 80            #    峰值有效性保护
SOPR_GREEN_LO, SOPR_GREEN_HI = 1.0, 1.3   # 🟢 正常区间
SOPR_OVERHEAT, SOPR_OVERHEAT_DAYS = 1.5, 5     # ⚠️ >1.5 持续 N 天
# SOPR_TURN_DAYS 定标（2026-08-24 同源回测）：2024-01~2025-10 牛市段 SOPR<1.0 连续最长仅 4 天，
# 5 天阈值 = 全牛市零误报；3 天会在牛市洗盘时误报 7 次。转折确认票宁准勿快。
SOPR_TURN, SOPR_TURN_DAYS = 1.0, 5              # 🔴 连续 N 天 <1.0（全网花费转亏=趋势铁证）
ETF_FOMO_WEEK_M = 3000.0    # ⚠️ 单周(5交易日)净流入 >$3B（FOMO 峰）
ETF_OUT_DAYS = 5            # 🔴 连续 N 个交易日净流出
ETF_OUT_TOTAL_M = -1000.0   #    且累计 < -$1B
FUNDING_GREEN = 0.0003      # 🟢 费率 <0.03%
FUNDING_WARN, FUNDING_WARN_DAYS = 0.0005, 7   # ⚠️ ≥0.05% 持续 7 天
OI_DROP_PCT = 0.15          # 🔴 OI 自近30天峰值骤降 >15%（爆仓潮子项暂无免费历史源）
RP_SPIKE_USD = 3e9          # 🔴 单日全网已实现利润 >$3B
RR_RISE_PCT = 0.5           # ⚠️ reserve-risk 7 日相对涨幅 >50%（快速上升）

HALVING_LAST = "2024-04-20"      # 第 4 轮减半（顶已于 2025-10-06 兑现 $126,200）
NEXT_HALVING_EST = "2028-04-20"  # 下轮减半估算（每 210,000 块 ≈ 4 年）
CYCLE_HIGH_RISK = (500, 555)     # 减半后第 500~555 天为顶部高危窗（近三轮终顶 526/548/534）


def _series(recent: list, field: str) -> list:
    """提取某字段的时间升序有效序列 [(date, value), ...]"""
    vals = [(m["date"], m.get(field)) for m in recent if m.get(field) is not None]
    return sorted(vals, key=lambda x: x[0])


def _top_dim_mvrz(recent: list) -> dict:
    """维度1 估值: MVRV Z-Score"""
    s = _series(recent, "mvrv_z")
    if not s:
        return {"key": "T1_mvrz", "name": "估值 MVRV-Z", "state": None,
                "value": None, "note": "无 mvrv_z 数据（bitcoin-data.com 未回填）"}
    z_now = s[-1][1]
    z_peak = max(v for _, v in s)
    peak_date = next(d for d, v in s if v == z_peak)
    if z_peak >= MVRV_Z_PEAK_MIN and (z_peak - z_now) / z_peak >= MVRV_Z_TURN_DROP:
        state, note = "turn", f"自峰值 {z_peak:.2f}({peak_date}) 回落 {(1-z_now/z_peak)*100:.0f}% ≥{MVRV_Z_TURN_DROP*100:.0f}%"
    elif z_now >= MVRV_Z_WARN:
        state, note = "warn", f"≥警戒线 {MVRV_Z_WARN}（历史双顶 7-9）"
    else:
        state, note = "green", f"<{MVRV_Z_WARN} 正常"
    return {"key": "T1_mvrz", "name": "估值 MVRV-Z", "state": state,
            "value": round(z_now, 3), "peak": round(z_peak, 3), "note": note}


def _top_dim_sentiment(recent: list) -> dict:
    """维度2 情绪: NUPL + 恐惧贪婪指数"""
    ns = _series(recent, "nupl")
    fs = _series(recent, "fear_greed_value")
    if not ns and not fs:
        return {"key": "T2_sentiment", "name": "情绪 NUPL+FG", "state": None,
                "value": None, "note": "NUPL 与 FG 均无数据"}
    nupl_now = ns[-1][1] if ns else None
    nupl_peak = max(v for _, v in ns) if ns else None
    fg_now = int(fs[-1][1]) if fs else None
    fg_peak = max(int(v) for _, v in fs) if fs else None

    # FG ≥85 连续天数（从最新往回数）
    fg_streak = 0
    if fs:
        for _, v in reversed(fs):
            if v >= FG_WARN:
                fg_streak += 1
            else:
                break

    parts = []
    state = "green"
    # 🔴 转折判定优先（慢变量只报状态，触发权在硬信号，但三态仍如实标出）
    if nupl_peak is not None and nupl_peak >= NUPL_WARN and nupl_now is not None \
            and nupl_now < NUPL_TURN_BACK:
        state = "turn"
        parts.append(f"NUPL 自 {nupl_peak:.2f} 跌回 {nupl_now:.2f}<{NUPL_TURN_BACK}")
    elif fg_peak is not None and fg_peak >= FG_PEAK_MIN and fg_now is not None \
            and fg_peak - fg_now >= FG_CRASH_DROP:
        state = "turn"
        parts.append(f"FG 自峰值 {fg_peak} 暴跌 {fg_peak-fg_now}≥{FG_CRASH_DROP} 点")
    elif nupl_now is not None and nupl_now >= NUPL_WARN:
        state = "warn"
        parts.append(f"NUPL {nupl_now:.2f}≥{NUPL_WARN}(Euphoria)")
    elif fg_streak >= FG_STREAK_DAYS:
        state = "warn"
        parts.append(f"FG≥{FG_WARN} 连续 {fg_streak} 天")

    val_parts = []
    if nupl_now is not None:
        val_parts.append(f"NUPL {nupl_now:.2f}")
    if fg_now is not None:
        val_parts.append(f"FG {fg_now}")
    if not parts:
        parts.append("正常区间")
    return {"key": "T2_sentiment", "name": "情绪 NUPL+FG", "state": state,
            "value": " / ".join(val_parts), "note": "; ".join(parts)}


def _top_dim_sopr(recent: list) -> dict:
    """维度3 获利了结: SOPR"""
    s = _series(recent, "sopr")
    if len(s) < SOPR_TURN_DAYS:
        return {"key": "T3_sopr", "name": "获利了结 SOPR", "state": None,
                "value": None, "note": f"SOPR 数据不足（{len(s)}天<{SOPR_TURN_DAYS}）"}
    tail = [v for _, v in s[-max(SOPR_TURN_DAYS, SOPR_OVERHEAT_DAYS):]]
    now = s[-1][1]
    if all(v < SOPR_TURN for v in tail[-SOPR_TURN_DAYS:]):
        state, note = "turn", f"连续 {SOPR_TURN_DAYS} 天 <{SOPR_TURN}（全网花费转亏=趋势铁证）"
    elif len(tail) >= SOPR_OVERHEAT_DAYS and all(v > SOPR_OVERHEAT for v in tail[-SOPR_OVERHEAT_DAYS:]):
        state, note = "warn", f">{SOPR_OVERHEAT} 持续 {SOPR_OVERHEAT_DAYS} 天（大幅获利了结）"
    else:
        state, note = "green", "正常区间" 
        if now > SOPR_GREEN_HI:
            note = f"{now:.3f} 处于 {SOPR_GREEN_HI}~{SOPR_OVERHEAT} 观察带（未达过热）"
    return {"key": "T3_sopr", "name": "获利了结 SOPR", "state": state,
            "value": round(now, 4), "note": note}


def _top_dim_etf(recent: list) -> dict:
    """维度4 机构资金: ETF 净流入（硬信号）"""
    days = [(m["date"], m["etf_net_flow_m"]) for m in recent if m.get("etf_net_flow_m") is not None]
    # recent 本身按日期倒序（get_recent_metrics ORDER BY date DESC）
    last_n = days[:ETF_OUT_DAYS]
    if len(last_n) < ETF_OUT_DAYS:
        return {"key": "T4_etf", "name": "机构资金 ETF", "state": None,
                "value": None, "note": f"ETF 数据不足（{len(last_n)}<{ETF_OUT_DAYS} 天，Tavily 半手动管道）"}
    flows = [v for _, v in last_n]
    week_sum = sum(flows)
    w20 = [v for _, v in days[:20]]
    inflow_days20 = sum(1 for v in w20 if v > 0)
    if all(v < 0 for v in flows) and week_sum <= ETF_OUT_TOTAL_M:
        state, note = "turn", f"连续 {ETF_OUT_DAYS} 日净流出累计 ${week_sum:,.0f}M ≤${ETF_OUT_TOTAL_M:,.0f}M"
    elif week_sum >= ETF_FOMO_WEEK_M:
        state, note = "warn", f"单周流入 ${week_sum:,.0f}M ≥${ETF_FOMO_WEEK_M:,.0f}M（FOMO 峰）"
    else:
        state, note = "green", f"近20交易日 {inflow_days20} 天正流入，近5日合计 ${week_sum:+,.0f}M"
    return {"key": "T4_etf", "name": "机构资金 ETF", "state": state,
            "value": f"{week_sum:+,.0f}M/周", "note": note}


def _top_dim_leverage(recent: list) -> dict:
    """维度5 杠杆结构: funding + OI（硬信号；爆仓潮子项暂无免费历史源，仅 OI 判定）"""
    frs = [m.get("funding_rate") for m in recent if m.get("funding_rate") is not None][:FUNDING_WARN_DAYS]
    conn = get_conn()
    cur = conn.execute("""SELECT date, oi_btc FROM oi_daily ORDER BY date DESC LIMIT 30""")
    oi_rows = cur.fetchall()
    conn.close()

    notes_missing = []
    if len(frs) < FUNDING_WARN_DAYS:
        notes_missing.append(f"funding 仅{len(frs)}天")
    if len(oi_rows) < 10:
        notes_missing.append("OI 历史不足")
    if len(frs) < FUNDING_WARN_DAYS and len(oi_rows) < 10:
        return {"key": "T5_leverage", "name": "杠杆 funding+OI", "state": None,
                "value": None, "note": "、".join(notes_missing)}

    fr_avg = sum(frs) / len(frs) if frs else None
    oi_vals = [(r["date"], r["oi_btc"]) for r in oi_rows if r["oi_btc"] is not None]
    oi_now = oi_vals[0][1]
    oi_peak = max(v for _, v in oi_vals)
    oi_mean = sum(v for _, v in oi_vals) / len(oi_vals)
    oi_drop = (oi_peak - oi_now) / oi_peak if oi_peak else 0
    oi_at_high = oi_peak >= oi_mean * 1.05  # 峰值确处"高位"

    state, note = "green", ""
    hard_bits = []
    if oi_at_high and oi_drop >= OI_DROP_PCT:
        state = "turn"
        hard_bits.append(f"OI 自30日峰 {oi_peak:,.0f} 骤降 {oi_drop*100:.0f}%（爆仓潮子项无数据源未计入）")
    elif fr_avg is not None and fr_avg >= FUNDING_WARN and oi_now >= oi_peak * 0.999:
        state = "warn"
        note = f"funding 7日均 {fr_avg*100:.4f}%≥0.05% 且 OI 创新高"
    elif fr_avg is not None and fr_avg >= FUNDING_WARN:
        state = "warn"
        note = f"funding 7日均 {fr_avg*100:.4f}%≥0.05%（OI 未创新高）"
    else:
        note = f"funding 7日均 {fr_avg*100:.4f}%<0.03%" if fr_avg is not None else "funding 数据不足"
    if hard_bits:
        note = "; ".join(hard_bits)
    return {"key": "T5_leverage", "name": "杠杆 funding+OI", "state": state,
            "value": f"{fr_avg*100:.4f}%" if fr_avg is not None else "-",
            "note": note}


def _normalize_realized_profit(vals: list) -> list:
    """realized-profit 端点单位自适应：若典型量级像'百万美元'则换算为美元"""
    med = sorted(vals)[len(vals)//2] if vals else 0
    return [v * 1e6 for v in vals] if 0 < med < 1e5 else vals


def _top_dim_distribution(recent: list) -> dict:
    """维度6 筹码派发: reserve-risk + 已实现利润"""
    rs = _series(recent, "reserve_risk")
    ps = _series(recent, "realized_profit")
    if not rs and len(ps) < 2:
        return {"key": "T6_distrib", "name": "筹码派发 RR+RP", "state": None,
                "value": None, "note": "reserve-risk 与 realized-profit 均无数据"}
    state, parts = "green", []
    if ps:
        pv = _normalize_realized_profit([v for _, v in ps])
        rp_now = pv[-1]
        if rp_now >= RP_SPIKE_USD:
            state = "turn"
            parts.append(f"单日已实现利润 ${rp_now/1e9:.2f}B ≥$3B（派发潮）")
        else:
            parts.append(f"已实现利润 ${rp_now/1e6:.0f}M/日")
    if rs and len(rs) >= 8:
        rr_now = rs[-1][1]
        rr_7ago = rs[-8][1]
        rise = (rr_now - rr_7ago) / rr_7ago if rr_7ago else 0
        if state != "turn" and rise >= RR_RISE_PCT:
            state = "warn"
        parts.append(f"reserve-risk {rr_now:.5f}{f'（7日+{rise*100:.0f}%）' if rise >= RR_RISE_PCT else ''}")
    return {"key": "T6_distrib", "name": "筹码派发 RR+RP", "state": state,
            "value": "-", "note": "; ".join(parts) or "正常"}


def cycle_clock() -> dict:
    """减半周期时钟：定位当前处于周期哪一段（主信号，不单独触发行动）"""
    today = date.today()
    last_h = date.fromisoformat(HALVING_LAST)
    next_h = date.fromisoformat(NEXT_HALVING_EST)
    days_since = (today - last_h).days
    days_to_next = (next_h - today).days
    lo, hi = CYCLE_HIGH_RISK
    risk_start = next_h + timedelta(days=lo)
    risk_end = next_h + timedelta(days=hi)

    if days_since > hi:
        phase = f"本轮已过顶（第 {days_since} 天 >{hi} 风险解除；顶 2025-10-06 $126,200 已兑现），见顶监控静默备勤"
    elif days_since >= lo:
        phase = f"本轮处于顶部高危窗第 {days_since} 天！"
    else:
        phase = f"本轮周期第 {days_since} 天，未入高危窗"
    return {
        "phase": phase,
        "next_halving": NEXT_HALVING_EST,
        "days_to_next_halving": days_to_next,
        "next_high_risk_window": f"{risk_start.isoformat()} ~ {risk_end.isoformat()}（减半后第 {lo}~{hi} 天）",
    }


def check_top_checklist() -> dict:
    """见顶清单 v2 主入口：六维三态 + 两级触发判定"""
    recent = get_recent_metrics(TOP_WINDOW)
    dims = [
        _top_dim_mvrz(recent),
        _top_dim_sentiment(recent),
        _top_dim_sopr(recent),
        _top_dim_etf(recent),
        _top_dim_leverage(recent),
        _top_dim_distribution(recent),
    ]
    overheat = sum(1 for d in dims if d["state"] == "warn")
    turn = sum(1 for d in dims if d["state"] == "turn")
    hard_keys = ("T4_etf", "T5_leverage")   # 资金流/杠杆类硬信号
    hard_turn = sum(1 for d in dims if d["state"] == "turn" and d["key"] in hard_keys)

    if turn >= 2 and hard_turn >= 1:
        level, action = "confirm", "🔴 顶部确认，执行离场"
    elif overheat >= 2:
        level, action = "region", "⚠️ 进入顶部区域，停止加仓"
    else:
        level, action = "normal", "🟢 正常（离场信号未触发）"

    data_ok = sum(1 for d in dims if d["state"] is not None)
    if data_ok < 4:
        action += f"（仅 {data_ok}/6 维度有数据，结果可信度低）"

    return {"overheat_cnt": overheat, "turn_cnt": turn, "hard_turn": hard_turn,
            "level": level, "action": action, "data_ok": data_ok,
            "dims": dims, "cycle": cycle_clock()}


def update_top_checklist_status() -> dict:
    """运行见顶检查并写入 top_checklist 快照"""
    result = check_top_checklist()
    conn = get_conn()
    conn.execute("""
    INSERT INTO top_checklist (date, signals_overheat, signals_turn, hard_turn, level, action, detail_json)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(date) DO UPDATE SET
        signals_overheat = excluded.signals_overheat,
        signals_turn = excluded.signals_turn,
        hard_turn = excluded.hard_turn,
        level = excluded.level,
        action = excluded.action,
        detail_json = excluded.detail_json
    """, (date.today().isoformat(), result["overheat_cnt"], result["turn_cnt"],
          result["hard_turn"], result["level"], result["action"],
          json.dumps({"dims": result["dims"], "cycle": result["cycle"], "data_ok": result["data_ok"]},
                     ensure_ascii=False)))
    conn.commit()
    conn.close()
    return result



SUPPORT_LOOKBACK = 180   # 前低识别窗口（天）
SWING_K = 5              # swing low：左右各 K 天
REBOUND_PCT = 0.15       # 显著性过滤：低点之后最大反弹 ≥ 15%


def detect_swing_low_support(days: int = SUPPORT_LOOKBACK, k: int = SWING_K,
                             rebound_pct: float = REBOUND_PCT) -> dict:
    """
    从价格序列识别最近一个合格的前低（swing low）

    合格定义：
    1. 该日收盘价低于左右各 K 天（右侧必须满 K 天 —— 最新 K 天内的新低不认，
       天然规避"仍在探底中"的误判）
    2. 低点之后的最大收盘价反弹 ≥ (1+rebound_pct)×低点价（滤掉阴跌中的小坑）

    返回:
        {"level", "anchor_date", "rebound_pct", "window_days"} 或
        {"level": None, "reason", "window_days"}
    """
    recent = get_recent_metrics(days)
    prices = sorted([(m["date"], m["price_usd"]) for m in recent if m.get("price_usd")])
    n = len(prices)
    if n < k * 2 + 1:
        return {"level": None, "reason": f"数据不足（需≥{k*2+1}天，现有{n}天）", "window_days": n}

    dates = [d for d, _ in prices]
    vals = [p for _, p in prices]

    candidates = []
    for i in range(k, n - k):
        if vals[i] < min(vals[i-k:i]) and vals[i] < min(vals[i+1:i+k+1]):
            future_max = max(vals[i+1:])
            rebound = future_max / vals[i] - 1
            if rebound >= rebound_pct:
                candidates.append({
                    "level": vals[i],
                    "anchor_date": dates[i],
                    "rebound_pct": rebound,
                })

    if not candidates:
        return {"level": None,
                "reason": f"无合格前低（窗口{n}天内无'低点后反弹≥{rebound_pct*100:.0f}%'的结构）",
                "window_days": n}

    best = max(candidates, key=lambda c: c["anchor_date"])  # 取最近的
    best["window_days"] = n
    return best


def update_auto_support() -> dict:
    """每日重算自动前低并维护 key_levels（值未变则跳过；manual 记录永不触碰）"""
    detected = detect_swing_low_support()
    new_level = detected.get("level")

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""SELECT level_value, anchor_date FROM key_levels
                      WHERE source='auto' AND active=1
                      ORDER BY anchor_date DESC LIMIT 1""")
    cur = cursor.fetchone()

    if new_level and cur and cur["anchor_date"] == detected["anchor_date"] \
            and abs(cur["level_value"] - new_level) < 1e-6:
        conn.close()
        return detected  # 与现役记录一致，跳过

    cursor.execute("UPDATE key_levels SET active=0 WHERE source='auto'")
    if new_level:
        cursor.execute("""INSERT INTO key_levels (level_type, level_value, source, anchor_date, note)
                          VALUES ('support', ?, 'auto', ?, ?)""",
                       (new_level, detected["anchor_date"],
                        f"swing-low自动识别: 此后反弹{detected['rebound_pct']*100:.1f}%, 窗口{detected['window_days']}天"))
    conn.commit()
    conn.close()
    return detected


def set_manual_support(level_value: float, anchor_date: str, note: str = ""):
    """人工设置前低（覆盖 auto，直到再次被人工清除/替换）"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE key_levels SET active=0 WHERE level_type='support' AND source='manual'")
    cursor.execute("""INSERT INTO key_levels (level_type, level_value, source, anchor_date, note)
                      VALUES ('support', ?, 'manual', ?, ?)""",
                   (level_value, anchor_date, note or "人工设置"))
    conn.commit()
    conn.close()


def get_active_support() -> dict | None:
    """当前生效的前低：manual 优先，否则最新 auto"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""SELECT * FROM key_levels WHERE level_type='support' AND source='manual' AND active=1
                      ORDER BY created_at DESC LIMIT 1""")
    row = cursor.fetchone()
    if row is None:
        cursor.execute("""SELECT * FROM key_levels WHERE level_type='support' AND source='auto' AND active=1
                          ORDER BY anchor_date DESC LIMIT 1""")
        row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def check_checklist() -> dict:
    """检查筑底清单状态，返回满足的信号数"""
    recent = get_recent_metrics(200)  # 覆盖前低识别窗口(180d)；ETF/funding 各取最近N条，不受窗口放大影响
    if not recent:
        return {"signals_met": 0, "details": "无数据"}

    latest = recent[0]
    details = {}

    # 1. MVRV 回落到 1.0-1.2 区间（CoinMetrics 有延迟，取最新可用值）
    mvrv = None
    mvrv_date = None
    for m in recent:
        if m.get("mvrv") is not None:
            mvrv = m["mvrv"]
            mvrv_date = m["date"]
            break
    if mvrv is not None:
        stale_days = (date.today() - date.fromisoformat(mvrv_date)).days
        details["1_mvrv"] = {
            "value": mvrv,
            "target": "1.0-1.2",
            "status": 1.0 <= mvrv <= 1.2,
            "as_of": mvrv_date,
            **({"stale_days": stale_days,
                "note": f"MVRV 已停更 {stale_days} 天，按 {mvrv_date} 旧值判定"}
               if stale_days > 7 else {}),
        }

    # 2. 恐惧贪婪 < 15（极度恐惧）
    fg = latest.get("fear_greed_value")
    if fg is not None:
        details["2_fear_greed"] = {
            "value": fg,
            "target": "<15",
            "status": fg < 15
        }

    # 3. ETF 滑动窗口净流入 >= 14 天（最近 20 个交易日，排除非交易日）
    etf_trading_days = [m for m in recent if m.get("etf_net_flow_m") is not None]
    window = etf_trading_days[:20]  # 最近 20 个交易日
    inflow_days = sum(1 for m in window if m["etf_net_flow_m"] > 0)
    details["3_etf_inflow"] = {
        "inflow_days": inflow_days,
        "total_trading_days": len(window),
        "target_days": 14,
        "window_days": 20,
        "status": inflow_days >= 14
    }

    # 4. 现货+永续需求（30 天累计净需求 > 0，且 7 日均值 > 0）
    fr_list = [(m["date"], m.get("funding_rate")) for m in recent if m.get("funding_rate") is not None]
    fr_values = [v for _, v in fr_list]

    if len(fr_values) >= 7:
        cumulative_30d = sum(fr_values[:30])  # 30 天累计
        avg_7d = sum(fr_values[:7]) / 7       # 7 日均值
        details["4_spot_perp"] = {
            "cumulative_30d": round(cumulative_30d, 8),
            "avg_7d": round(avg_7d, 8),
            "data_days": len(fr_values),
            "status": (cumulative_30d > 0) and (avg_7d > 0)
        }
    else:
        details["4_spot_perp"] = {
            "cumulative_30d": 0,
            "avg_7d": 0,
            "data_days": len(fr_values),
            "status": False,
            "error": "数据不足"
        }

    # 5. 二次探底不破低（事件触发型）
    details["5_second_test"] = _check_second_test(recent)

    # 6. 时间窗口（2026-Q4）
    today = date.today()
    in_window = (today.year == 2026 and today.month >= 10) or today.year > 2026
    details["6_time_window"] = {
        "current": today.isoformat(),
        "target": "2026-Q4",
        "status": in_window
    }

    signals_met = sum(1 for v in details.values() if v.get("status"))
    return {"signals_met": signals_met, "details": details}


def _check_second_test(recent: list) -> dict:
    """
    二次探底不破低（事件触发型）

    逻辑：
    1. 识别上涨腿：从近期低点到反弹高点
    2. 识别回踩腿：从反弹高点到价格进入前低上方 10% 观察区
    3. 硬条件：回踩最低价不破前低（key_levels 表动态获取）
    4. 软条件：缩量判定
       - 回踩腿日均现货量 < 上涨腿日均现货量 × 80%
       - 回踩期间无单日现货量 > 20日均量 × 150% 的放量下跌日
    5. 信号分级：
       - 价格不破 + 缩量达标 → ✅ 强信号
       - 价格不破 + 缩量未达标但未恐慌 → ⚠️ 中性
       - 价格不破 + 放量恐慌 → ❌ 降级
       - 价格破位 → ❌ 失败
    """
    support_row = get_active_support()
    if support_row is None:
        detected = detect_swing_low_support()
        return {
            "status": False,
            "signal": "⏳",
            "reason": f"无有效前低：{detected.get('reason', 'key_levels 为空')}",
            "target": "待形成",
        }

    SUPPORT = support_row["level_value"]
    OBSERVE_ZONE = SUPPORT * 1.10  # 观察区：前低上方 10%
    VOLUME_RATIO_THRESHOLD = 0.80  # 缩量判定：回踩 < 上涨 × 80%
    PANIC_VOLUME_RATIO = 1.50      # 恐慌放量：> 20日均量 × 150%
    WARNING_ZONE = SUPPORT * 1.15  # 提示区：前低上方 15%（不算通过）

    support_meta = {
        "support_level": SUPPORT,
        "support_source": support_row["source"],      # 'manual' | 'auto'
        "support_anchor": support_row["anchor_date"],
        "target": f">= ${SUPPORT:,.0f}",
    }

    # 按日期正序排列
    data = sorted(recent, key=lambda x: x["date"])

    # 提取价格和现货量
    prices = [(m["date"], m["price_usd"]) for m in data if m.get("price_usd")]
    volumes = [(m["date"], m.get("spot_volume")) for m in data if m.get("spot_volume")]

    if len(prices) < 10:
        return {"status": False, "signal": "❌", "reason": "价格数据不足"}

    # --- 识别上涨腿和回踩腿 ---
    # 找近期低点（作为上涨腿起点）
    min_price = min(prices, key=lambda x: x[1])
    min_idx = prices.index(min_price)
    min_date, min_val = min_price

    # 找低点之后的高点（作为上涨腿终点 / 回踩腿起点）
    prices_after_min = prices[min_idx:]
    max_price = max(prices_after_min, key=lambda x: x[1])
    max_idx = prices.index(max_price)
    max_date, max_val = max_price

    # 当前价格
    current_date, current_price = prices[-1]

    # --- 检查回踩是否已进入观察区 ---
    in_observe_zone = current_price <= OBSERVE_ZONE
    in_warning_zone = current_price <= WARNING_ZONE

    if not in_observe_zone and not in_warning_zone:
        # 价格还没跌到观察区，回踩未发生
        return {
            "status": False,
            "signal": "⏳",
            "reason": f"回踩未发生（当前 ${current_price:,.0f}，需跌至 ${OBSERVE_ZONE:,.0f} 以内）",
            "current_price": current_price,
            "observe_zone": OBSERVE_ZONE,
            "rally_high": max_val,
            "rally_low": min_val,
            **support_meta,
        }

    # --- 回踩已进入观察区，进行判定 ---
    # 回踩腿：从高点到当前
    pullback_prices = [p for d, p in prices if d >= max_date]
    pullback_low = min(pullback_prices)

    # 硬条件：不破低
    price_held = pullback_low >= SUPPORT

    # --- 缩量判定 ---
    volume_result = _check_volume_condition(data, max_date, min_date, VOLUME_RATIO_THRESHOLD, PANIC_VOLUME_RATIO)

    # --- 信号分级 ---
    if not price_held:
        signal = "❌"
        reason = f"破位（最低 ${pullback_low:,.0f} < ${SUPPORT:,.0f}）"
        status = False
    elif volume_result.get("data_missing"):
        # 数据缺失：诚实降级为"待验证"，绝不冒充中性/达标（P2 修复）
        signal = "⏳"
        reason = f"价格守住 ${SUPPORT:,.0f}，但缩量待验证（{volume_result.get('panic_detail') or '现货量数据缺失'}）"
        status = False
    elif volume_result["panic_detected"]:
        signal = "⚠️"
        reason = f"放量恐慌（即使守住也降级）: {volume_result['panic_detail']}"
        status = False  # 降级观察
    elif volume_result["volume_ok"]:
        signal = "✅"
        reason = f"强信号：缩量回踩不破低"
        status = True
    else:
        signal = "⚠️"
        reason = f"中性：未破低但缩量不达标（等待缩量确认）"
        status = False  # 中性，等待确认

    return {
        "status": status,
        "signal": signal,
        "reason": reason,
        "current_price": current_price,
        "pullback_low": pullback_low,
        "rally_high": max_val,
        "rally_low": min_val,
        "volume": volume_result,
        **support_meta,
    }


def _check_volume_condition(data: list, pullback_start_date: str, rally_start_date: str,
                            volume_threshold: float, panic_threshold: float) -> dict:
    """
    检查缩量条件

    返回:
    - volume_ok: 缩量是否达标
    - panic_detected: 是否检测到恐慌放量
    - details: 详细数据
    """
    result = {
        "volume_ok": False,
        "panic_detected": False,
        "panic_detail": None,
        "data_missing": True,   # 缺数据时保持 True，进入判定后置 False
        "rally_avg_volume": 0,
        "pullback_avg_volume": 0,
        "volume_ratio": 0,
        "ma20_avg_volume": 0,
    }

    # 提取现货量数据
    volumes = [(m["date"], m.get("spot_volume")) for m in data if m.get("spot_volume")]
    if len(volumes) < 20:
        result["panic_detail"] = f"现货量数据不足（{len(volumes)}/20 天）"
        result["data_missing"] = True
        return result

    result["data_missing"] = False

    # 计算 20 日均量（全序列含周末）
    vol_values = [v for _, v in volumes]
    ma20_avg = sum(vol_values[-20:]) / 20
    result["ma20_avg_volume"] = ma20_avg

    # 上涨腿日均量
    rally_vols = [v for d, v in volumes if rally_start_date <= d <= pullback_start_date]
    rally_avg = sum(rally_vols) / len(rally_vols) if rally_vols else 0
    result["rally_avg_volume"] = rally_avg

    # 回踩腿日均量
    pullback_vols = [v for d, v in volumes if d >= pullback_start_date]
    pullback_avg = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 0
    result["pullback_avg_volume"] = pullback_avg

    # 缩量判定：回踩 < 上涨 × 80%
    if rally_avg > 0:
        ratio = pullback_avg / rally_avg
        result["volume_ratio"] = round(ratio, 4)
        result["volume_ok"] = ratio < volume_threshold

    # 恐慌放量检测：回踩期间无单日 > 20日均量 × 150%
    panic_threshold_val = ma20_avg * panic_threshold
    panic_days = [(d, v) for d, v in volumes if d >= pullback_start_date and v > panic_threshold_val]

    if panic_days:
        result["panic_detected"] = True
        panic_detail = ", ".join([f"{d}({v/1e9:.1f}B)" for d, v in panic_days[:3]])
        result["panic_detail"] = f"{len(panic_days)}天放量: {panic_detail}"

    return result


def update_checklist_status():
    """更新筑底清单状态表"""
    result = check_checklist()
    today = date.today().isoformat()

    conn = get_conn()
    cursor = conn.cursor()

    details = result["details"]
    action = "观望"
    if result["signals_met"] >= 3:
        action = "可开始分批建仓"

    # 提取二次探底状态字符串
    second_test = details.get("5_second_test", {})
    second_test_status = second_test.get("signal", "⏳")

    cursor.execute("""
    INSERT INTO checklist_status (date, signals_met, mvrv_status, mvrv_value,
                                  fear_greed_status, fear_greed_value,
                                  etf_inflow_days, spot_perp_days,
                                  second_test_status, time_window_status, action)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(date) DO UPDATE SET
        signals_met = excluded.signals_met,
        mvrv_status = excluded.mvrv_status,
        mvrv_value = excluded.mvrv_value,
        fear_greed_status = excluded.fear_greed_status,
        fear_greed_value = excluded.fear_greed_value,
        etf_inflow_days = excluded.etf_inflow_days,
        spot_perp_days = excluded.spot_perp_days,
        second_test_status = excluded.second_test_status,
        time_window_status = excluded.time_window_status,
        action = excluded.action
    """, (
        today,
        result["signals_met"],
        details.get("1_mvrv", {}).get("status", 0),
        details.get("1_mvrv", {}).get("value"),
        details.get("2_fear_greed", {}).get("status", 0),
        details.get("2_fear_greed", {}).get("value"),
        details.get("3_etf_inflow", {}).get("inflow_days", 0),
        details.get("4_spot_perp", {}).get("data_days", 0),
        second_test_status,
        details.get("6_time_window", {}).get("status", 0),
        action
    ))

    conn.commit()
    conn.close()
    return result


def log_signal(signal_type: str, signal_desc: str, value: float, threshold: float, triggered: bool):
    """记录信号触发日志"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO signal_log (date, signal_type, signal_desc, value, threshold, triggered)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (date.today().isoformat(), signal_type, signal_desc, value, threshold, int(triggered)))
    conn.commit()
    conn.close()


def log_collect(source: str, status: str, message: str = ""):
    """记录数据采集日志"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO collect_log (date, source, status, message)
    VALUES (?, ?, ?, ?)
    """, (date.today().isoformat(), source, status, message))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("\n📊 筑底清单状态:")
    result = check_checklist()
    print(f"  满足信号数: {result['signals_met']}/6")
    for k, v in result.get("details", {}).items():
        status = "✅" if v.get("status") else "❌"
        print(f"  {status} {k}: {v}")
