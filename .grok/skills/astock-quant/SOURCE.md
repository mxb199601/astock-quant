# 机制来源（不整包安装）

对照全文：`docs/skill-github对照.md`。本 skill 只收机制，不收外仓 CLI / 数据源 / 荐股话术。

| 机制 | 来源 |
|------|------|
| 薄路由 + 按需加载 | Howard-Jerry/quant-agent-skills, lululu811/zettaranc-skill |
| 先跑 CLI，禁止补猜数字 | shouldnotappearcalm/a-share-skill, er6y/stockquant |
| 方向/regime 硬过滤 | 本仓 `action_today` + a-share-skill MACD 共振选股的「方向不对不进强候选」 |
| WAIT / 仅观察 ≠ 买点；失效条件 | Howard-Jerry closeout, Haochenhust/ch-skills |
| OPT 禁止当实盘；T+1/成本/幸存者 | 本仓 `stress` + shakeebshaan/claude-code-quant-skills |
| AI 建议必须可复盘 | ZICXR/A-Stock-Skills trade-journal → 本仓 `discipline` / `daily` |
| 一个入口覆盖 90% | ZICXR「少即是多」（10 个核心而非 29 个平铺） |
| MCP 包装本仓 CLI，不接 akshare | GitHub A股 MCP 多为行情层；本仓 `mcp_server.py` |
