# 周易「逢3必变」高低点研究计划 · 2026-10-13

> 研究工具，**不构成投资建议 / 非荐股 / 非交易终端**。  
> 默认池：沪深主板非 ST（剔 300/688/北交/ST）。主结论看 **PATH**，OPT 仅水分对照。

用户诉求顺序：先形式化「逢3必变」→ 再看支撑位 → 再看峰结构 → 大量测试能否标出局部低点/高点。

---

## 1. 可复用资产（调研摘要）

| 模块 | 可复用点 | 本策略用法 |
|------|----------|------------|
| `lib/chan.py` | 合并K、分型、笔、中枢；无前视确认日 | **不直接依赖**缠论买卖点；峰/摆动思想可参考分型确认 |
| `lib/patterns.py` | 可复现形态、无未来函数 | 不改 registry；峰/支撑自建 |
| `lib/forecast.py` | 相似日涨跌（边弱 ~52–54%） | **不接入**联合闸门；本模块独立 |
| `lib/up_prob.py` | 固定持有上涨概率、主板抽样、CSV/MD 输出 | 回测报告口径对齐 |
| `lib/stress.py` / `metrics.py` | `exit_path_daily`、`summarize_returns` | PATH 前瞻收益 |
| `lib/data.py` | `list_cached_symbols` / `load_kline_csv` | 只读缓存 K 线 |
| `docs/缠论最小规格.md` | 确认日、无前视纪律 | 摆动确认规则对齐同一哲学 |

**硬约束**：不改写 MENGα / joint / screen 主栈；本模块为 **P3 观察/研究层**。

---

## 2. 「逢3必变」操作化定义（去神秘化）

传统语意：「事物发展到第三次，往往转向」。落到 OHLC：**同向摆动极值第三次确认后，标记反转候选**。

### 候选编码

| ID | 名称 | 规则摘要 | 优缺点 |
|----|------|----------|--------|
| **A（v1 主选）** | **三连同向极值** | 交替 zigzag 上，连续 3 个**同侧**摆动低点单调不升（或 3 个摆动高点单调不降）；第 3 个在右侧确认日触发 | 规则清晰、易回测；对窗口敏感 |
| B（对照） | 三触支撑/阻力 | 近窗内价格 3 次触及同一 SR 带（±band%）且第 3 次未有效突破 | 贴「三碰」直觉；带参更多、易过拟合 |

### v1 主定义（编码 A）写死

1. **摆动确认**（无前视）  
   - 参数 `swing_k`（默认 **3**）：中心棒 `i` 的 high 为 `[i-k,i+k]` 严格最高 → 摆动高；low 严格最低 → 摆动低。  
   - **确认日** = `i + swing_k`（右侧 K 收盘后才知）。  
2. **Zigzag**：摆动点按时间排序；同向取更极端；强制顶底交替。  
3. **逢3触发**  
   - 取 zigzag 中最近 3 个**底** `L1,L2,L3`（时间递增）。若 `L3.price ≤ L2.price ≤ L1.price`（允许相等），则在 **L3 确认日** 发出 `pred=LOW`。  
   - 最近 3 个**顶** `H1,H2,H3` 若 `H3.price ≥ H2.price ≥ H1.price`，在 H3 确认日发出 `pred=HIGH`。  
4. **同一确认日**若极端情况下双信号，丢弃（极少）。

编码 B 仅在计划中保留为 v2 对照，**本回合不实现**。

---

## 3. 信号栈（按用户顺序）

```
Layer A  逢3必变触发（三连同向极值）
    │
Layer B  支撑/阻力邻近：pred=LOW 时 L3 距近窗摆动低簇 ≤ near_pct；
         pred=HIGH 时 H3 距近窗摆动高簇 ≤ near_pct
    │
Layer C  峰结构：近 2 段摆动是否 HH/HL（多头结构）或 LH/LL（空头结构）
    │
输出     label ∈ {LOW, HIGH} + conf ∈ {高, 中, 低}   【仅研究】
```

### 置信度

| conf | 条件 |
|------|------|
| 高 | A 触发 ∧ B 邻近 ∧ C 与反转方向一致（LOW 前为空头结构 LL/LH；HIGH 前为多头 HH/HL） |
| 中 | A ∧ (B ∨ C) |
| 低 | 仅 A |

### 输出字段（事件级）

`sym, date, pred, conf, trigger_price, support_or_resist, structure, swing_k, ...`

---

## 4. 数据

- 源：`cache/kline_sina/*.csv`（date,open,high,low,close,vol）  
- 池：`list_cached_symbols(mainboard_only=True, drop_st=True)`  
- 最少 K 线：≥ 120（建议回测 ≥ 200）  
- **不拉券商、不荐股、不写 action**

---

## 5. 评价指标（诚实口径）

设信号日 index=`i`，前瞻窗 `N`（默认 5/10）、持有 `M`（默认 10/20）。

| 指标 | 定义 |
|------|------|
| **hit_extreme@N** | LOW：`min(low[i:i+N+1])` 相对 `trigger_price` 的下探幅度 ≤ `mae_tol`（默认 2%），即接近抓住窗内最低；HIGH 对称用 max(high) |
| **hit_reversal@N** | LOW：其后 N 日内出现确认的反向摆动高，或 `close[i+N] > close[i]`；HIGH 对称 |
| **MAE** | LOW：`(trigger − min_low_window) / trigger × 100`（越小越好）；HIGH：`(max_high − trigger)/trigger×100` |
| **PATH 前瞻** | LOW：自 `close[i]` 做多，`exit_path_daily(..., tight, M)`；HIGH：报告做多 PATH **与** 固定持有收益（A股难融券，HIGH 的「看跌命中」以价格下跌占比为主，不假装可空） |
| **基线** | 随机日 / 任意摆动确认日 的同指标，避免自嗨 |

**禁止**：用 OPT 乐观窗当主结论；宣称必涨必跌。

---

## 6. 非目标（Out of scope）

- 不进 `action_today` / `joint` / MENGα 主路径  
- 不实现编码 B、不等间距「易数」日历、不附会卦辞  
- 不接经纪商、不推送荐股、不 git push  
- 不改 MES light-daq  
- v1 不做线段/中枢背驰（那是缠论模块）

---

## 7. 验收（Acceptance）

1. 计划文档落盘本路径 + box 副本。  
2. `lib/feng3.py` 可对单票检测摆动/逢3/SR/结构并产出标签。  
3. `scripts/backtest_feng3.py` 可对缓存主板批量跑，写出 `output/feng3/` CSV + 摘要 HTML/MD。  
4. 首轮回测给出：n、hit@N（高/低）、PATH 均值；**若边弱诚实写明**。  
5. 可选：`cli.py feng3` 薄封装，不污染主栈。

---

## 8. 实现与运行

```bash
cd "/Users/meng/测试/量化工具"
/Users/meng/测试/.venv/bin/python scripts/backtest_feng3.py --sample 800 --seed 42
# 或
/Users/meng/测试/.venv/bin/python cli.py feng3 --sample 800
```

参数默认：`swing_k=3`, `near_pct=2.0`, `N=10`, `M=20`, `mae_tol=2.0`, PATH exit=`tight`, cost=`15`bps。

---

## 9. 文件清单

| 路径 | 作用 |
|------|------|
| `docs/plans/2026-10-13-yijing-feng3-hl.md` | 本计划 |
| `lib/feng3.py` | 检测 + 标签 |
| `scripts/backtest_feng3.py` | 批量 PATH/命中回测 |
| `output/feng3/` | 事件 CSV、汇总 |

---

## 10. 一句话

把「逢3必变」落成 **第三次同向摆动极值确认后的局部高低点候选**，再用支撑邻近与峰结构分层置信，用 PATH + hit@N 检验有没有薄边——没有就当观察层，不升格。


---

## 11. 首轮回测结果（2026-10-13，已落盘）

样本：主板缓存 **800** 只（seed=42），`swing_k=3`，`hit_n=10`，`hold_m=20`，PATH=`tight`/15bps。

| 集合 | n | hit_extreme% | PATH胜率% | PATH均值% | 固定下跌% |
|------|---|--------------|-----------|-----------|-----------|
| 逢3 LOW | 12660 | 78.50 | 44.58 | −0.209 | — |
| 任意摆动低（基线） | 53938 | 77.64 | 43.95 | −0.229 | — |
| 逢3 HIGH | 10909 | 80.52 | — | — | 56.42 |
| 任意摆动高（基线） | 53906 | 78.83 | — | — | 53.79 |

**结论**：相对任意摆动基线增量极薄（hit ~+1pt，PATH 几乎持平）。**首轮无独立可交易边**；保持观察/研究层，不升格 joint/action。明细见 `output/feng3/`。
