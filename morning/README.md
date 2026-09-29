# morning/ — Crypto 晨报与信号体系（6+1 币 × 3 体系）

每日 08:05（Asia/Shanghai）由 QwenPaw cron 推送的加密货币信号晨报，
以及配套的回测引擎与研究脚本存档。监控池：BTC/ETH/SOL/BNB/AR/DOGE + UNI。

## 三体系口径

### A · 金叉 v2 定稿（2026-09-21 拍板）
- 信号：MA20 上穿 MA60，金叉日 MA20 斜率 ∈ [0, 0.5] %/根、MA60 斜率 ∈ (-0.10, 0.25]（走平带），冷却 10 天
- 建仓：信号次日收盘（信号日涨幅 >5% 顺延）
- 卖出状态机：-10% 硬止损全程；浮盈 <100% 时破 MA20 卖半 / 破 MA60 清仓；浮盈曾 ≥100% 转 trail（回吐最高利润 1/3 清仓）；双边费用 0.1%
- v2 定稿 22 笔：f20 +6.12%/中位 +7.79%/胜率 68.2%，完整 +25.0%，净值 21.23x（牛市专属策略，熊市年份全负）
- **参数贴原 5 币池长，扩池即衰减（已证），勿扩**

### B · 超跌起爆（A股 2026-09-06 口径平移）
- 信号：偏离 60 日线 ≤ -20%（等比放量的 watch 档）或 ≤ -30%（watch+），MA60 斜率首次 >0 的当日触发
- 卖出：收盘 < max(买价×0.9, 含当根 30 日低) 全清

### C · 上穿 MA60 观察铃（B版策略·2026-09-29 上线，当晚改版终版）
- 只提醒不建模拟仓；N（连续站住天数）由用户人工判断
- 收盘价上穿 MA60（判据与回测同口径：收盘 vs 当日 MA60）→ 第 1 天预警（附回测警示）
  → 连续站住第 2/3 天各推确认 → 收盘跌回推作废；**无冷却，每次上穿必报**
- 回测结论（2026-09-29）：7 币 6 年，N=3 档 f20 中位 +5.95%（唯一中位为正组合）；
  正增量=BTC/UNI/CRV（深坑翻身型），负增量=SOL/DOGE（强趋势型）；当天追入是最差执行档（CRV 6年 0.38x）
- CRV 已由用户拍板移出监控

## 目录

```
morning/
├── morning_report.py            # cron 入口：数据拉取 → 三体系信号 → 推送
├── bt_goldencross_v2.py         # 引擎：金叉 v2（morning_report import signals/run_trade/st）
├── bt_oversold.py               # 引擎：超跌起爆（import scan_signals）
├── bt_price_cross.py            # 引擎：价格上穿 MA60 回测（体系C口径来源）
├── test_b_watch.py              # 观察铃回归测试（历史回放 + 合成场景）
├── engines/                     # 历史回测引擎存档（v1/v3/4h/multi_tf/pullback/steep，非运行依赖）
├── research/                    # 一次性研究脚本存档（UNI/CRV 分析、画图、fetch）
└── data/                        # 运行时与数据缓存（.gitignore 不入库）
    ├── morning_state.json       #   晨报台账：已推送信号、模拟持仓、手工仓、观察铃状态
    └── klines_*.json            #   K 线缓存（research 脚本可重建）
```

## 运行

```bash
# 晨报（cron 每日 08:05，QwenPaw job）
cd /home/application/crypto-research/morning && ../venv/bin/python morning_report.py

# 手动 dry-run（不写状态、不推送）
../venv/bin/python morning_report.py --dry-run

# 观察铃回归测试
../venv/bin/python test_b_watch.py
```

依赖：pandas/numpy（库根 venv）；Binance 行情走 data-api.binance.vision 镜像（api.binance.com 不通）。
`data/` 整目录不入库；`fetch_klines_1d_ext.py`、`fetch_uni_crv.py`（research/）可重建缓存。

## 历史教训（勿重蹈）

1. **扩池即衰减**：定稿参数贴原 5 币调出，铺到 10 币立刻衰减（胜率 59%→37%）
2. **UNI/CRV 与金叉体系不兼容**：带筛 6 年 0 信号（高波动 DeFi 币金叉结构性出带），裸金叉 6 年净值 0.50x
3. **f20 与完整交易的背离**：吃得住 20 天行情 ≠ 吃得住到 20 天的过程，止损磨损决定真实体验
4. **牛市专属**：金叉 v2 在 2018/2019/2022/2026 环境全负，环境开关不开不挂档
