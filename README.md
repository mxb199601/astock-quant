# 量化工具

个人 A 股**研究**工具集（结构评估 / 回测验证），**不是**交易终端，**不荐股**。

**默认股票池：沪深主板 A 股**（600/601/603/605、000/001/002/003），  
**已剔除：创业板(300*)、科创板(688*)、ST/*ST、北交所。**

核心立场与手册一致：**没有能稳定选出赢家的公式**；能控制的是「何时参与 + 仓位 + 纪律」。

### 算法文档（先读）

| 文档 | 内容 |
|------|------|
| **[docs/算法全景.md](docs/算法全景.md)** | 全系统算法地图、决策栈、证据等级、scan/factors 漂移 |
| [docs/MENGα自研指标.md](docs/MENGα自研指标.md) | MENGα v5 构成与 IC |
| [docs/压力测试报告.md](docs/压力测试报告.md) | OPT vs PATH |
| [docs/LEGACY.md](docs/LEGACY.md) | 根目录遗留脚本（勿当主策略） |
| [docs/缠论最小规格.md](docs/缠论最小规格.md) | 缠论 v1 可程序化定义（观察/否决，非买入） |
| [docs/边沿优化.md](docs/边沿优化.md) | 还能靠什么：PATH 阶梯 + 组合纪律 + boost |
| [docs/破戒检查.md](docs/破戒检查.md) | 纪律审计日报：系统自检 + 持仓破戒 + holdings 助手 |
| **Agent Skill / MCP** | [`.grok/skills/astock-quant/`](.grok/skills/astock-quant/) · `mcp_server.py`（stdio 包装 `cli.py`；对照 [docs/skill-github对照.md](docs/skill-github对照.md)） |
| **对话提示词** | [docs/提示词.md](docs/提示词.md)（复制即用；不要贴网上荐股模板） |

**操作口径：** 红灯空仓 → 绿灯看 `joint`（完整通过∩MENGα）→ 出场看 **PATH + tight**，勿信 OPT 七成。

## 相对市面量化工具，这里对齐了什么

| 常见能力 | 本工具 | 说明 |
|----------|--------|------|
| 统一 CLI | `cli.py` | status / refresh / score / screen / backtest / report |
| 本地行情缓存 | `cache/kline_sina/` | 新浪日线 + 近似前复权 |
| 强制刷新 | `cli.py refresh` | 避免用过期缓存做决策 |
| 大盘择时 | 红绿灯 MA20/MA60 | 与手册一致 |
| 选股/打分 | 公式三 + 一票否决 + 加分 | 含 RSI、放量确认 |
| 回测成本 | 默认单边 15bps | 往返扣成本后再统计 |
| 风险指标 | 胜率 / 盈亏比 / Sharpe / MDD / 超额 | 事件型，非完整组合回测 |
| **稳健模式 v5** | `--mode robust` + `--exit tight` | 反热 + **默认指数低波** + TP4/SL3 |
| **关闭低波** | `--no-low-vol` | 退回仅反热带（信号更多、边更薄） |
| 高胜率 v3（OPT好看） | `--mode elite` + `--exit wr` | 勿当实盘胜率 |

```bash
$PY cli.py status                          # 含今日是否指数低波
$PY cli.py score 600519 --mode robust      # v5 默认低波
$PY cli.py score 600519 --mode robust --no-low-vol
$PY cli.py backtest --mode robust --exit tight --no-plot
$PY cli.py screen --mode robust
```
| 结果页 | `cli.py report --open` | HTML 汇总 |

### 算法 v3（胜率优化，主板 800 股抽样，持仓 20 日，扣成本）

| 入场 × 出场 | n | 胜率 | 均值 | Sharpe |
|-------------|---|------|------|--------|
| strict × asym(TP8/SL5) | 3210 | 56.3% | +1.92% | 0.31 |
| **elite × wr(TP5/SL3) 默认** | **1277** | **70.6%** | **+2.35%** | **0.64** |
| elite × tight(TP4/SL3) | 1277 | **78.4%** | +2.19% | 0.76 |
| winrate × wr | 906 | 68.8% | +2.20% | 0.59 |
| strict × wr | 3210 | 70.4% | +2.32% | 0.64 |

关键结论：**胜率主要由出场不对称决定**（asym→wr 约 +14pt）；入场 elite 略优。  
明细：`output/winrate_optimize.csv`

```bash
$PY cli.py backtest --mode elite --exit wr       # 默认高胜率
$PY cli.py backtest --mode elite --exit tight    # 极致胜率
$PY cli.py backtest --mode strict --exit asym    # 旧口径对照
```

**红灯仍应空仓；不构成投资建议。** wr/tight 会提前兑现利润，单笔上限低于 asym。

### 压力测试（必读）

```bash
$PY cli.py stress              # 乐观窗 OPT vs 逐日路径 PATH
```

| 口径 | elite×wr 约胜率 | 约均值 | 说明 |
|------|-----------------|--------|------|
| OPT 乐观窗（默认 backtest） | ~74% | ~+2.6% | 窗内极值 + 先止盈，**高估** |
| PATH 路径（同日先损） | ~44% | ~+0.2% | 更接近实盘 |
| PATH + 成本50bps / 熊市 | ~39–44% | **负** | 压力下失效 |

详见 `docs/压力测试报告.md`、`output/stress_test.csv`。  
**相对比较可用 OPT；仓位/预期请用 PATH。**

### 经典公开公式库

```bash
$PY cli.py formulas list       # 双重动量/海龟/金叉/布林/MACD/共振…
$PY cli.py formulas compare    # 本地缓存横向回测排名
$PY cli.py formulas scan --theme   # 今日谁触发了哪些公式
$PY cli.py backtest --mode dual_mom   # 单公式回测
```

**刻意没做的**（避免做成半吊子券商）：实盘下单、Level2、机器学习选股、自动荐股、必涨名单、通达信收费黑盒源码。

## 快速开始

验证：`for t in scripts/test_*.py; do /Users/meng/测试/.venv/bin/python "$t" || break; done`（全部脚本退出码 0 为过门）

### Web 系统（推荐）

```bash
cd /Users/meng/测试/量化工具
/Users/meng/测试/.venv/bin/python app.py
# 浏览器自动打开 http://127.0.0.1:8765
```

界面功能：大盘红绿灯 · 完整通过/结构观察 · 个股打分。

### 算法栈 v5（2026-08）— 详见 [算法全景](docs/算法全景.md)

| 档位 | 含义 |
|------|------|
| 今日操作 | 红灯空仓；绿灯才出联合/完整通过 |
| 联合信号 ★★ v2 | **完整通过 ∩ MENGα 冷静**（弃用涨跌研究闸门） |
| 完整通过 ★ | 大盘多头 + robust 结构 + 质量分 |
| MENGα v5 | IC 对齐权重：反热为主 |
| 冷静池 v2 | 高α + 芽≥40 + 风险过滤（观察非买） |
| 涨跌研究 | 边弱 ~52–54%，**仅描述统计**，不作买入 |

```bash
$PY cli.py action                  # 唯一操作口径
$PY cli.py recap --cached          # 复盘报告（网上骨架×本仓纪律）
$PY cli.py joint
$PY cli.py alpha --top 40
$PY cli.py cool --top 30
$PY cli.py forecast 600428 --no-industry   # 可选，研究价值低
$PY cli.py hist-validate --sample 600      # 多窗口历史验证 → output/hist_validate_summary.md
$PY cli.py up-prob --sample 600            # 固定持有1/3/5/10/20日上涨概率
```

Web：`python app.py` → 今日操作 / 联合 / MENGα。**不是荐股。**  
历史验证摘要：`output/hist_validate_summary.md`。
### 每日自动（收盘后）

```bash
# 安装：每个工作日 15:35 自动刷新+扫盘+macOS通知
$PY cli.py schedule install

# 立刻试跑一次
$PY cli.py daily --open

# 查看/卸载
$PY cli.py schedule status
$PY cli.py schedule uninstall
```

报告目录：`output/daily/YYYY-MM-DD.md` 与 `output/daily/latest.md`  
有「完整通过」时会弹系统通知；红灯/黄灯时通知「联合0·完整0…」。
**点击通知会打开当日报告**（依赖 `terminal-notifier`，已 `brew install terminal-notifier`；未装则退回 osascript，点不开）。

### 命令行

```bash
cd /Users/meng/测试/量化工具
PY=/Users/meng/测试/.venv/bin/python

$PY cli.py serve                    # Web
$PY cli.py daily                    # 日更
$PY cli.py status
$PY cli.py refresh --universe       # 全主板非ST K线
$PY cli.py score 600519 002371
$PY cli.py screen
$PY cli.py backtest
```

兼容旧入口：`score_stocks.py`、`screen_market.py`、`advanced_theme_backtest.py` 仍可直接跑。

## 目录

```
量化工具/
├── cli.py                 # 统一入口
├── lib/
│   ├── data.py            # 拉数 / 缓存 / 大盘状态
│   ├── factors.py         # 公式三 / 打分
│   ├── metrics.py         # 成本 / Sharpe / MDD
│   ├── backtest.py        # 主题回测引擎
│   ├── report.py          # HTML 报告
│   └── themes.py          # 主题观察池
├── docs/                  # 手册与执行清单
├── cache/kline_sina/      # K 线 + 基本面
└── output/                # CSV / 图 / dashboard.html
```

## 日常流程（摘要）

1. `status`：红灯 → 降仓/空仓，不硬做  
2. `refresh --theme`：更新数据  
3. `screen --theme` 或通达信公式三  
4. `score` 复核一票否决 + 加分  
5. 人工核消息 / 资金 / 业绩  
6. 分散 + 买入即止损（见 `docs/执行清单.md`）

## 依赖

```
pip install -r requirements.txt
```

numpy / pandas / matplotlib

## 免责

仅供个人规则校验与历史回测研究。得分高 ≠ 会涨。投资决策自负。
