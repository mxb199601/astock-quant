---
name: astock-quant
description: >
  A股量化工具（MENGα）操作规程。用户问能不能买、今天怎么做、选股/扫盘/回测/打分/复盘/破戒、
  大盘红绿灯、公式三，或 /astock-quant 时使用。
when-to-use: >
  A股量化、选股、扫盘、回测、打分、今日操作、MENGα、大盘红绿灯、联合信号、冷静池、持仓破戒。
  不要用：美股港股期货加密、代下单接券商、纯改无关代码。
---
# 量化工具 · A股研究规程

```
ROOT=/Users/meng/测试/量化工具
PY=/Users/meng/测试/.venv/bin/python
```

本仓是**研究工具**，不是交易终端。默认池=沪深主板非 ST（无创业板 / 科创板 / 北交所）。
算法事实源：`docs/算法全景.md`。命令参数源：`$PY cli.py <cmd> -h`。

**调数优先 MCP**（`astock_status` / `astock_action` / …，入口 `mcp_server.py`）。MCP 未连接时退回 CLI。不要另装 akshare MCP，会和本仓缓存双源。

## Step 0 · 新鲜度与红绿灯

优先 MCP `astock_status`；否则在 `ROOT` 执行：

```bash
$PY cli.py status
```

完成判据：stdout 同时出现「红绿灯」和「上次刷新」日期。

- `上次刷新` 的日历日 < 最近交易日 → 先 `$PY cli.py refresh --all`（`--universe` 只在用户点名全市场时用，很慢）
- 盘中指数看 `status` / `live`（实时）；扫盘名单仍是日线缓存，完整截面等收盘后 `daily`
- 未同时站上 MA20+MA60（红灯/黄灯不够）→ 操作口径=**空仓**，后面只跑观察层

## Step 1 · 先分类再动手

| 用户要的 | 命令 | 超时 |
|---------|------|------|
| 今天怎么做 / 能不能买 / 操作 | `action` | 300s |
| 复盘报告 / 每板块几只 / 少妇仓位 / 游资槽位 | `recap --cached`（已有日报）或 `recap` | 300s |
| 大盘 / 缓存 | `status`；指数或个股快照 `live [代码…]` | 60s |
| 这只票打分 | `score CODE --mode robust` | 60s |
| 联合 / 完整通过 | `joint` / `screen --mode robust` | 300s |
| MENGα / 冷静池 | `alpha --top 40` / `cool --top 30` | 300s |
| 持仓 / 破戒 / 纪律复盘 | `holdings check` 或 `discipline` | 120s |
| 回测 / 胜率 | `backtest --mode robust --exit tight --no-plot` | 300s |
| 压力 / 水分 | `stress --sample 400` | 300s |
| 形态 / 公式 / 缠论 / 妖股 | `patterns` / `formulas scan` / `chan` / `yaogu` | 180s |
| 日更 | `daily` | 600s |

输出文件见 `references/cli-map.md`。没列出的参数跑 `-h`，不凭记忆补。

一次只跑路由命中的命令。禁止为了「看起来全面」连跑 `hist-validate` / `refresh --universe`。

## Step 2 · 决策栈（买入向）

```
regime(MA20+MA60) ──红灯──► action=空仓, buy=[]
                 ──绿灯──► full_pass(robust) ∩ MENGα冷静 = joint
                              └── action_today 候选
```

与 `lib/action_today.py` 一致：

- 买入向出口**只有** `action`
- MENGα / 冷静池 / 形态 / 妖股 / 缠论 / 涨跌研究 / 经典公式 **永不单独**当买入候选
- MACD / 金叉 / 突破等技术信号是观察层，不是主策略

完成判据：回答里的操作字段只能是 `空仓` / `可关注候选` / `仅观察` / `破戒` 之一，且与本轮 `action`（或 `discipline`）输出一致。

## Step 3 · 数字从 CLI 来

1. 跑命令，读 stdout + 对应 `output/*.csv|md`
2. 关键字段缺失 → 写「缺失/低置信」，不补猜
3. 列出任何代码时附**证据卡**：代码、命中哪一层（joint / full_pass / 观察）、本轮命令、输出文件
4. 名单非空时写一段**反方**：这份名单在什么路径下会亏（红灯切换、PATH 先止损、行业拥挤、成本抬到 50bps）
5. 每个「可关注」给一条**失效条件**（跌破结构、触及 tight 止损、大盘转红）

`仅观察` / 等待更好价格 / 不新开 **不是买点**。

## Step 4 · 诚实口径

- 仓位、预期、「能不能做」→ **PATH + tight**（逐日路径；同日双触先止损）
- `backtest` 默认 OPT 乐观窗会高估胜率；禁止把 OPT 数说成实盘胜率
- 「拿满 N 日会涨」走 `up-prob`，与 PATH 止盈止损不是同一命题
- 精确数字以本轮输出或 `docs/算法全景.md` §4–8 为准，不默写记忆中的胜率

完成判据：只要提到胜率或均值，必须标明 OPT 还是 PATH，并给出本轮文件路径。

## 输出合同

1. 操作口径（空仓 / 可关注 / 仅观察 / 破戒）
2. 证据卡（命令 + 文件）
3. 反方与失效条件（仅当名单非空）
4. 免责一次：「研究工具，不构成投资建议。」

## 失败退路

| 情况 | 做法 |
|------|------|
| 刷新失败或 `仍是旧数据>0` | 报告失败/滞后数，不把旧缓存当今日截面 |
| 绿灯但 action 空 | 写明 joint=0 / full_pass=0，不把 α/形态顶成买入名单 |
| 命令超时 | 缩样本、改 `--theme` / `--cached`，不编结果 |
| 用户要下单或接券商 | 停在分析与纪律；本仓不下单 |

## 按需加载

| 材料 | 何时读 |
|------|--------|
| `references/cli-map.md` | 不确定输出文件或子命令 |
| `docs/算法全景.md` | 改算法口径、对证据等级 |
| `docs/破戒检查.md` | 持仓 / 纪律 |
| `docs/MENGα自研指标.md` | 解释 α 构成 |
| `docs/压力测试报告.md` | 用户追问 OPT/PATH 水分 |
| `lib/*.py` | 用户要改实现；默认不把源码读进上下文 |

根目录 `niugu_backtest*.py` 等遗留脚本见 `docs/LEGACY.md`，不当主策略。
