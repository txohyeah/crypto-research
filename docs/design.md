# Web3 每日信息推送 · 设计方案 v0.1

> 2026-08-25 初稿。约束：**信息源全免费**、小资金个人使用、每天一条消息一分钟读完。

---

## 1. 目标与非目标

**目标**
- 每天 1 条汇总推送：「重大事件」+「值得关注的机会」+「市场数据速览」
- 机会发现以**结构化数据规则**驱动为主，不靠刷新闻碰运气
- 信息源 100% 免费（公开 RSS / 免费 API / 公开页面）

**非目标（v1 明确不做）**
- 秒级突发告警（第二期再议）
- Twitter/X KOL 原生抓取（API 贵、替代方案不稳定）
- Telegram 频道爬虫（噪音过滤成本高）
- 自动化交易信号（已有 BTC monitor 项目，不重复建设）

---

## 2. 信息源清单（已于 2026-08-25 实测验证）

### L1 快讯层（重大事件）

| 源 | 接入方式 | 实测 | 代理 | 说明 |
|---|---|---|---|---|
| 律动 BlockBeats | 开放API `api.theblockbeats.news/v1/open-api/open-flash` | ✅200 | 不需要 | 中文快讯最快之一，JSON 结构化，比 RSS 更好解析 |
| Odaily 星球日报 | RSS `odaily.news/rss` | ✅200 | 不需要 | 中文，快讯+深度混合 |
| The Block | RSS `theblock.co/rss.xml` | ✅200 | 不需要 | 英文，机构向，融资报道多 |
| Cointelegraph | RSS `cointelegraph.com/rss` | ✅200 | 不需要 | 英文大众向 |
| CoinDesk | RSS `coindesk.com/arc/outboundfeeds/rss/` | ⚠️需代理 | 需要 | 英文主流，走代理即可 |
| SEC Press Releases | RSS `sec.gov/news/pressreleases.rss` | ✅200 | 不需要 | 监管黑天鹅一手源头（诉讼/ETF） |

**淘汰记录**：PANews RSS（rss.panewslab.com 本机不可达）；RootData 直连 403 反爬（列为 P2 可选）。

> 取舍逻辑：中文 2 个 + 英文 2 个 + SEC，足够覆盖。大事件重复率极高，靠 LLM 合并去重，源多了只是浪费。

### L2 机会层（结构化数据，核心差异化）

| 源 | 接入方式 | 实测 | 用途 |
|---|---|---|---|
| DeFiLlama protocols | 免费API `api.llama.fi/protocols` | ✅200 | R1 新协议冷启动 / R2 TVL 异动 |
| DeFiLlama yields | 免费API `yields.llama.fi/pools` | 同基础设施 | R3 高收益池异动 |
| crypto-fundraising.info | 页面解析 | ✅200 | R4 融资事件风向 |
| Galxe campaigns | 页面抓取（无官方免费API） | 未测 | R5 空投任务窗口（不稳定，备人工策展兜底） |
| ETHGlobal / DoraHacks / 各生态 Grants 页 | 定期检查 | 未测 | R6 Hackathon/Grant 日历 |

### L3 市场温度层

| 源 | 接入方式 | 实测 | 代理 |
|---|---|---|---|
| alternative.me 恐惧贪婪 | API | ✅200 | 否 |
| Binance 资金费率/爆仓 | API（btc_monitor 已在用） | ✅ | 是 |
| CoinGecko 价格 | 免费API | ⚠️需代理 | 是 |

---

## 3. 机会雷达（规则引擎，本方案的核心）

「新机会」不依赖新闻碰运气，用可解释、可调参的规则从免费数据里挖：

| # | 规则 | 数据源 | 参数（初版） |
|---|---|---|---|
| R1 | **新协议冷启动**：近期上架 + TVL 达标 | llama `/protocols` | listedAt ≤ 7天 且 TVL ≥ $1M |
| R2 | **TVL 异动**：叙事升温信号 | llama `/protocols` | 7日增速 ≥ 100% 且 TVL ≥ $10M |
| R3 | **高收益池**：APR 异常（必带风险提示） | yields pools | APR 前 10 且 TVL ≥ $5M |
| R4 | **融资风向**：一级市场投了什么赛道 | crypto-fundraising | 近 48h 单笔 ≥ $5M，按赛道聚合 |
| R5 | **空投/任务窗口**：免费可参与的 campaign | Galxe 等 | fee=0 且截止在未来 7 天 |
| R6 | **Grant/Hackathon 日历**：builder 赚钱机会 | ETHGlobal/DoraHacks/生态 grants | 周更检查 |

> 设计观点：R1/R2/R4 是纯数据规则，稳定可靠，是主力；R5 抓取脆弱，先用「每周我人工策展一次」兜底，跑通后再考虑自动化；R6 对 builder 身份的你价值最高但频率低，周更即可。

---

## 4. 系统架构（两阶段演进）

```
┌─────────────────────────────────────────────────────┐
│ Phase A（第1~2周，快速验证）：QwenPaw cron 驱动        │
│                                                     │
│  cron(每日08:00) → collector.py 采集入库              │
│       → 韩立(LLM)打分分类去重                         │
│       → renderer 渲染 → 飞书 webhook 推送            │
└─────────────────────────────────────────────────────┘
                       ↓ 格式满意后固化
┌─────────────────────────────────────────────────────┐
│ Phase B（长期）：独立脚本 + 系统 crontab               │
│  collector → 规则粗筛 → LLM API 打分 → 推送           │
│  （模式与 crypto_trading/scripts 一致，脱离会话依赖）   │
└─────────────────────────────────────────────────────┘
```

**目录规划**
```
web3_push/
├── data/push.db          # SQLite
├── scripts/
│   ├── collect.py        # 采集：RSS/API → 入库
│   ├── score_render.py   # 打分渲染（Phase B 里接 LLM API）
│   └── health.py         # 源健康检查
└── docs/design.md        # 本文档
```

### 六个核心对象（v0.2 确认）

| 对象 | 职责 | 有状态？ |
|---|---|---|
| **Source 源** | 从哪采、怎么采（新闻 RSS 与数据 API 统一抽象） | ✅ 健康记录 |
| **Item 条目** | 流经全流水线的一等公民，快讯/TVL异动/融资统一结构 | ❌ 纯数据 |
| **Evaluator 评估器** | `evaluate(items) → scored_items`，无状态纯函数 | ❌ |
| **Digest 早报** | 内容决策（选中哪些条目、怎么分栏），先于格式存在 | ❌ 聚合产物 |
| **Channel 频道** | 只管投递，不管内容 | ✅ 投递记录 |
| **Run 运行实例** | 一次任务的边界；`run_id=日期` 天然幂等 | ✅ 全局状态 |

关键约束：
- **Store 是唯一有状态模块**，其余环节皆可独立重跑（拿历史 items 重放 Evaluator 不必重新采集）
- **Evaluator 禁止碰网络**：只吃给定条目吐结果，幻觉控制是架构隔离而非 prompt 恳求
- render 与 deliver 分离：加新渠道（邮件等）零改动内容决策

**Channel 落地实现（2026-08-25 确认）**
- 不新建 webhook：复用 QwenPaw 实例已绑定的 feishu channel（app cli_a93…，与 StockResearch 同一应用）
- 投递命令：`qwenpaw channels send --agent-id default --channel feishu --target-user ou_152f… --target-session 59e9e09b`（目标会话=「飞书专用频道」，唯一 feishu 会话，已实测送达）
- Phase A 的调度载体是 **agent 型 cron**：08:00 唤醒本 agent → 会话内执行 collect.py + digest_raw.py → LLM 评估渲染 → 最终回复由框架自动投递到目标会话。即 Phase A 中 render/deliver 由框架代劳，Channel 对象在 Phase B 固化时再显式建模块

**存储 Schema（SQLite，v0.2 细化）**
```sql
-- 条目（一等公民）
CREATE TABLE items (
  id TEXT PRIMARY KEY,      -- sha1(source + url)
  source TEXT, title TEXT, url TEXT,
  published_at INT, fetched_at INT,
  category TEXT,            -- 监管/安全事件/宏观流动性/项目大事/机会-新协议/机会-融资/机会-空投/机会-grant/其他
  importance INT,           -- 1-10
  reason TEXT, action TEXT, -- 为什么重要 / 能做什么
  run_id TEXT,              -- 哪次 Run 采集/评估的（追溯锚点）
  pushed_at INT             -- NULL = 未推送
);
CREATE INDEX idx_items_pub ON items(published_at);
CREATE INDEX idx_items_cat ON items(category, importance);

-- 运行实例（幂等锚点）
CREATE TABLE runs (
  date TEXT PRIMARY KEY,    -- '2026-08-26'：同日重跑直接跳过已 delivered 的
  state TEXT,               -- collected → evaluated → rendered → delivered / failed
  stats TEXT,               -- {"raw":180,"kept":52,"pushed":9}
  errors TEXT, started_at INT, finished_at INT
);

-- 源健康登记
CREATE TABLE sources (
  name TEXT PRIMARY KEY, endpoint TEXT, need_proxy INT,
  enabled INT, last_ok_at INT, fail_count INT
);

-- 投递记录
CREATE TABLE push_log (
  run_date TEXT, channel TEXT, status TEXT,
  error TEXT, delivered_at INT
);
```

**存储设计要点**
- **WAL 模式**：Phase A 我人肉评估时要边写边读，避免锁冲突
- 单写者日批负载 → SQLite 唯一短板（高并发写）不存在；容量 ~100MB/年，三年免清理
- 备份 = 复制单个文件；与技术栈里 btc_monitor.db 一致
- **不做 FTS 全文索引**（StockResearch 的 FTS5 中文分词教训）；查询模式仅需时间窗+分类+评分，普通索引够用
- 未来语义去重若需要向量，`sqlite-vec` 扩展在同一文件内加列即可，不引入新数据库

**淘汰的替代方案**
| 方案 | 淘汰理由 |
|---|---|
| 纯 JSON/CSV 文件 | 跨天去重、幂等查询退化成手写 join |
| Postgres/MySQL | 单写者日批任务养一个数据库服务不值 |
| 独立向量库 | 过早优化，sqlite-vec 可覆盖 |

**库里只存事实**（采到什么、评成什么、推没推）；prompt 模板在代码里，设计文档在 markdown，原始 HTML 快照不存（调试可重抓）。

---

## 5. 处理流水线

1. **时间窗**：东八区昨天 08:00 → 今天 08:00
2. **粗筛（规则，省 token）**：关键词黑名单杀广告软文（sponsor/ad/活动营销）；标题相似度去重（分词后 Jaccard > 0.6 合并）
3. **LLM 精筛**：对粗筛后条目（预计 30~60 条）批量打分
   - 输出：importance(1-10)、category、一句话 reason、action 建议
   - **幻觉约束**：只允许基于给定条目归纳，reason 必须对应来源，禁止引入外部"知识"
   - 阈值：importance ≥ 6 进「重大事件」；category 以「机会」开头且 ≥ 5 进「机会雷达」
4. **渲染推送**

**成本估算**：日增原始条目约 100~200，粗筛后 ~50 条 × 平均 80 token ≈ 4k input + 2k output/天。Phase A 由我直接处理，零额外成本；Phase B 即使用付费 LLM API 也 < ¥0.1/天。

---

## 6. 推送格式（草案）

```
📰 Web3 早报 | 2026-08-26

▍重大事件
1.〔监管〕SEC 起诉 xxx —— 影响：xxx 赛道合规风险上升
2.〔安全〕xxx 协议被攻击损失 $xxM —— 涉及链上合约 0x..，注意同源协议
3.〔宏观〕...

▍机会雷达
1.〔新协议〕xxx（7日TVL $0→$14M）做什么：xxx｜门槛：xxx｜风险：未审计
2.〔融资〕RWA 赛道 48h 内 3 笔 ≥$5M 融资，关注 xxx
3.〔Grant〕Solana 基金会 xxx 方向开放申请，截止 xx

▍数据速览
BTC $60,120 (+0.8%) | ETH $3,210 | 恐惧贪婪 52 中性 | 24h爆仓 $0.9亿

— 源状态：7/8 正常（CoinDesk 走代理正常）
```

原则：每条都带「所以呢」——要么说清影响，要么给行动建议，不做纯标题罗列。

---

## 7. 可靠性设计

- **源健康检查**：每次采集更新 sources 表；连续 3 次失败标记 degraded 并在推送尾部报告，绝不静默吞掉
- **代理策略**：直连优先，失败自动换 Clash 代理（127.0.0.1:7890）重试一次
- **跨天去重**：URL hash 主键硬去重 + 已推送条目标题相似度软合并（同一事件第二天不再单独成条，只在有新增事实时更新）
- **降级**：某源挂了当天照常推送，缺哪块明说

---

## 8. 分期计划

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0**（第1周） | 建 web3_push 目录 + collect.py + SQLite + 源健康检查；先推「未经打分的 top 条目」验证覆盖面 | 每天能看到原始素材 |
| **P1**（第2周） | LLM 打分 + 机会雷达 R1/R2/R4 上线 + 推送格式打磨 | 完整版早报 |
| **P2**（可选） | 固化 Phase B 独立脚本；R5 空投自动化；RootData 反爬突破；突发即时告警 | 长期稳定运行 |

---

## 9. 待确认问题

1. **推送渠道**：飞书？（StockResearch 已验证过通道；新建独立 webhook 还是用同一个机器人？）
2. **语言**：英文源内容翻译成中文摘要输出？（默认：是）
3. **推送时间**：每天 08:00？
4. **路径确认**：Phase A 跑两周、满意后固化成 Phase B 独立脚本？

## 变更记录
- v0.2 (2026-08-25)：合入六对象模型（Source/Item/Evaluator/Digest/Channel/Run + Store 单点有状态）；细化存储设计（runs/push_log 表、WAL、不做FTS、sqlite-vec 演进路径、替代方案淘汰理由）
- v0.1 (2026-08-25)：初稿，含 13 个候选源的实测验证结果
