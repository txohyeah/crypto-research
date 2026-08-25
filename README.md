# crypto-news

Web3 每日信息推送：重大事件 + 新机会雷达，每天 08:00 推送一条一分钟读完的 HTML 早报。

完整设计方案见 [docs/design.md](docs/design.md)。

## 当前状态：v1 运行中

每日流水线：采集入库 → Evaluator 筛选（Phase A 为 agent，Phase B 可换 LLM API）
→ deliver_digest 一站式校验/渲染/投递/记账。cron 触发，同日幂等不重推。

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # 填写代理等配置（凭据不入库）
python3 scripts/collect.py                          # 采集
echo '{"date":"2026-01-01"}' | python3 scripts/render_digest.py   # 渲染试验
```

## 结构（六对象 → 代码落点）

```
config/sources.json        # Source 注册表（声明式，含每个源的实测状态）
scripts/db.py              # Store：SQLite schema（WAL），唯一有状态模块
scripts/collect.py         # Source 执行器：直连优先/代理重试、入库即去重、Run 记账
scripts/digest_raw.py      # Evaluator 的输入契约：时间窗条目清单（只粗排不判断）
scripts/market.py          # 行情快照（BTC/ETH/恐惧贪婪），供渲染注入
scripts/render_digest.py   # Digest schema + validate() + HTML 渲染（接缝①输出端）
scripts/send_feishu_file.py# Channel：纯投递，不管内容与记账
scripts/deliver_digest.py  # 投递编排：幂等检查→校验→快照→渲染→投递→runs/push_log 落账
```

关键设计约束：

- **Evaluator 禁止碰网络**——只吃给定条目，幻觉控制靠架构隔离而非 prompt 恳求
- **render 与 deliver 分离**——加新渠道零改动内容决策
- **run_id=日期做幂等**——已 delivered 的 run 重跑直接跳过；采集重跑不会降级其状态

## 已知问题

- 中文快讯源（律动/Odaily/PANews）2026-08-25 实测全部失效，见 sources.json 各 note，
  修复路径：注册 BlockBeats 开放平台 key，或自建 RSSHub。
