# GitHub A股量化 Skill 对照（2026-09-06）

本仓没有现成 `SKILL.md`。下面按 **stars（当日 `gh api`）** 看公开 A 股 / 量化 skill，用 writing-for-agents 的四问决定「收机制、不装箱」。

落地物：`.grok/skills/astock-quant/`（Grok / Claude 均可发现）。

## 样本（只读原文，未安装）

| 仓库 | ★ | 它实际是什么 | 对本仓 |
|------|---|----------------|--------|
| [qusong0627/QuantMind](https://github.com/qusong0627/QuantMind) | 1393 | 量化平台（Qlib / 多市场 / Docker），skills 绑它自己的 QuantDB | 另一套系统，不搬 |
| [lululu811/zettaranc-skill](https://github.com/lululu811/zettaranc-skill) | 459 | 万千交易纪律 skill；CLI `zt`；T+1 模拟；质量门 | **机制最好**，角色/荐股/创业板与本仓冲突 |
| [lzwme/finance-quant-skills](https://github.com/lzwme/finance-quant-skills) | 340 | akshare / baostock / tushare / backtrader / QMT **工具文档 skill 包** | 数据层会和本仓 sina 缓存打架 |
| [shouldnotappearcalm/a-share-skill](https://github.com/shouldnotappearcalm/a-share-skill) | 235 | `a-share-data` + 模拟盘；另有 MACD 选股 | 数据脚本路由可学；模拟下单不收 |
| [ZICXR/A-Stock-Skills](https://github.com/ZICXR/A-Stock-Skills) | 23 | 10 个分层 skill；trade-journal 让 AI 被复盘 | 「少即是多」+ 问责 |
| [Howard-Jerry/quant-agent-skills](https://github.com/Howard-Jerry/quant-agent-skills) | 2 | 投研编排（~8k 行）：WAIT≠买、失效条件、结论不进交易 | 编排过重，语义合同可学 |
| [er6y/stockquant](https://github.com/er6y/stockquant) | 6 | Python 出数据+hint，LLM 按 TASK 推导 | 「禁止读 6000 行源码、禁止发明数字」 |
| [shakeebshaan/claude-code-quant-skills](https://github.com/shakeebshaan/claude-code-quant-skills) | 1 | 回测审计 / 策略对抗 | 与本仓 PATH vs OPT 同方向 |
| [atompilot/baostock-skill](https://github.com/atompilot/baostock-skill) | 17 | BaoStock API 坑 | 本仓不换数据源 |
| [jangviktor-web/a-stock-data-quant](https://github.com/jangviktor-web/a-stock-data-quant) | 22 | akshare+MyTT 工具箱 | 能力重叠，口径不同 |

## 四问（整包不装）

1. **这个问题在本体系由谁负责？** 数据/扫盘/回测/纪律 = 本仓 `cli.py` + `lib/*` + `docs/算法全景.md`。真空位是 **agent 操作规程**（何时跑哪条命令、数字从哪来、OPT 不许冒充实盘）。
2. **现有负责人做得到吗？** CLI 做得到计算；agent 过去没有 skill，会默写胜率、把 MENGα 当买入名单、红灯仍荐股。
3. **否决理由能被原文推翻吗？**
   - zettaranc「只覆盖 A 股」看起来能用，但其 SKILL.md 明确含创业板/科创板（「何时不加载」节写「仅限 A 股（主板/创业板/科创板）」），与本仓 README 剔除 300/688 冲突，否决成立。
   - finance-quant-skills 不是选股引擎，是 **第三方库说明书**；本仓已有新浪日线缓存，再装 akshare/tushare skill 会双源。否决成立。
   - a-share-skill 的 paper-trading 是模拟下单账户；本仓 README「刻意没做实盘下单」。否决成立。
   - QuantMind 是平台不是 skill 规程。否决成立。
4. **真正增量（收进本 skill 的机制）：**

| 机制 | 原文位置 | 本仓为何表达不了 |
|------|----------|------------------|
| 薄路由 + 按需加载 reference | zettaranc `SKILL.md`「运行时资源索引」；Howard-Jerry「Load References」 | 本仓文档全、agent 会整本读进上下文 |
| 先 CLI 再开口；缺字段写缺失 | a-share-data `SKILL.md`「执行流程 / 不要做的事」；stockquant description | 没有强制「数字必须带本轮命令」 |
| 方向硬过滤，节奏再漂亮也不进强候选 | macd-trend-resonance Step 2；本仓已有红绿灯，但 skill 没写给 agent | 代码里有，agent 不知道 |
| `仅观察` / WAIT ≠ 买点；必须给失效条件 | Howard-Jerry Non-Negotiable Core Gates §6 | `action` 有空仓，agent 仍会把观察池说成推荐 |
| 反方 / 魔鬼代言人 | stockquant「置信度+证据卡+魔鬼代言人」 | 本仓输出表，没有强制反方段落 |
| OPT 禁止当实盘；成本/T+1/幸存者 | 本仓 `docs/压力测试报告.md`；shakeebshaan `/backtest-review` | 数字在文档里，agent 仍背 OPT 七成 |
| AI 建议必须可复盘 | A-Stock-Skills `trade-journal/SKILL.md` 哲学句 | 已有 `discipline`/`daily`，缺「回答必须指向这些文件」 |
| 一个入口覆盖日常 90% | A-Stock-Skills README「少即是多」 | 避免做成 10 个平铺 skill |

## MCP（2026-09-06 补）

GitHub 上的 A 股 MCP 几乎全是 **akshare/baostock 行情层**（如 cn-financial-mcp、ashare-mcp、mcp-aktools、FinQ4Cn-mcp-server）。对本仓：

- 数据层已有 `cache/kline_sina` + `cli.py`，再接 akshare MCP = 双源
- 它们给「现价/财报」，不给本仓的红绿灯 / `action` / PATH
- 部分带 `trading_suggest` 荐股，和本仓冲突

本仓 MCP 是 **stdio 包装 `cli.py`**（`mcp_server.py`，零新依赖），skill 仍管纪律。

## 明确不收

- 角色扮演、少妇战法、B1/B2 荐股话术（zettaranc）
- 模拟/实盘下单、券商/QMT/MiniQMT（a-share-paper-trading、finance-quant-skills）
- 换数据源到 akshare/tushare/baostock 当主路径（与 `cache/kline_sina` 双源）
- 六 agent 辩论、五文件研报包（Howard-Jerry，绑定作者 vault）
- 把 MACD/金叉当主策略（与 `docs/股票操作手册.md`「追涨突破无可靠 edge」相反）
